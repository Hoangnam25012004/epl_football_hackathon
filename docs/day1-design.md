# Detailed Design — Ngày 1 (21/10): Schema · Synthetic Match Engine · Match State · Stats Tools

> Liên quan: [claude.md](../claude.md) (tổng quan dự án) · [checklist.md](../checklist.md) (mục Ngày 1)
> Phiên bản: v1.2 (as-built, 20/10/2026) · Người thực hiện: 1 người · Ngôn ngữ code: Python 3.12
> Thay đổi ở v1.1: kiểm soát bóng tính **theo thời gian bóng lăn** (thêm event `ball_dead`, `ball_recovery` và máy trạng thái bóng, mục 4.4); chốt 4 quyết định ở mục 17.
> Thay đổi ở v1.2: tài liệu được cập nhật theo code thực tế sau khi triển khai và hiệu chỉnh; các điểm khác với v1.1 được tổng hợp ở **mục 18**.

---

## 0. Mục tiêu và kết quả cuối ngày

Ngày 1 xây **nền dữ liệu** cho toàn bộ dự án. Mọi tính năng ở Ngày 2–7 (Claim Check, talking points, lower-third, bình luận tiếng Việt) đều đọc số liệu từ các module viết hôm nay. Vì vậy hai yêu cầu quan trọng nhất là **đúng** và **tái lập được**; tốc độ chỉ xếp sau.

**Kết quả cuối ngày (Definition of Done):**

```powershell
cd backend
uv run pundit replay --scenario dominant_loser --seed 42 --speed 20   # replay trận mẫu ra console
uv run pundit stats  --scenario dominant_loser --seed 42 --window H2  # bảng số liệu hiệp 2
uv run pytest                                                          # tất cả test xanh
uv run ruff check . ; uv run ruff format --check . ; uv run mypy       # lint + type check sạch
```

Các file phải có trong repo khi kết thúc ngày:

| Đường dẫn | Nội dung |
|---|---|
| `backend/src/pundit/**` | Code: domain, stats, state, engine, CLI |
| `backend/tests/**` | Test các tầng (mục 12) |
| `packages/schemas/*.schema.json` | JSON Schema xuất từ Pydantic, frontend dùng ở Ngày 4 |
| `data/matches/dominant_loser-42.events.jsonl` + `.meta.json` | Trận demo đã sinh sẵn |
| `backend/tests/golden/*` | Hash và tóm tắt số liệu của các trận demo |
| `.github/workflows/ci.yml` | CI chạy lint, type check, test trên Ubuntu và Windows |

**Ngoài phạm vi Ngày 1** (để tránh phình việc):
- Tracking data đầy đủ (vị trí 22 cầu thủ theo từng khung hình)
- Sự kiện `carry` / `dribble` / `clearance`, phản lưới nhà, phạt đền phát sinh ngẫu nhiên
- Bất kỳ lời gọi LLM, API web hay UI nào

---

## 1. Nguyên tắc thiết kế chống bug

| # | Nguyên tắc | Áp dụng cụ thể |
|---|---|---|
| P1 | **Một nguồn sự thật duy nhất** | Event log là danh sách **chỉ thêm, không sửa** (append-only). Tỷ số, thống kê và snapshot đều **tính lại từ log** bằng hàm thuần (không trạng thái ẩn), không lưu bản sao ở nơi khác |
| P2 | **Tất định (deterministic)** | Cùng `seed` thì ra cùng từng byte. Không dùng `random` toàn cục, `datetime.now()`, `uuid4()`, `hash()`, và không duyệt `set` khi chọn ngẫu nhiên |
| P3 | **Trạng thái sai thì không biểu diễn được** | Pydantic v2 với discriminated union theo `type`, `frozen=True`, `extra="forbid"`, ràng buộc miền giá trị bằng `Field(...)` |
| P4 | **Mỗi chỉ số chỉ có một định nghĩa** | Mọi metric đi qua một `registry`. Kịch bản, CLI, agent (Ngày 2) và MCP đều gọi cùng hàm này |
| P5 | **Kiểm tra tại biên** | Mọi event đi vào hệ thống phải qua `MatchState.apply()` để kiểm tra invariant. Generator cũng tự kiểm tra output bằng chính bộ luật đó |
| P6 | **Hằng số đặt ở một chỗ** | Kích thước sân, ngưỡng final third, ngưỡng tốc độ… chỉ nằm trong `domain/pitch.py` và `domain/thresholds.py` |
| P7 | **Báo lỗi rõ ràng, không im lặng** | Đội hoặc cầu thủ không tồn tại thì `raise`, không trả về 0. Thiếu dữ liệu thì trả `status="insufficient_data"` |
| P8 | **Làm tròn chỉ ở tầng hiển thị** | `value` lưu số thực nguyên bản; hàm `display()` mới làm tròn. Toạ độ và xG được làm tròn **lúc sinh** để file JSON ổn định |

---

## 2. Tooling và cấu trúc thư mục

### 2.1. Công cụ

| Công cụ | Lựa chọn | Lý do |
|---|---|---|
| Python | **3.12** (ghim `>=3.12,<3.13`) | Golden hash chỉ đảm bảo trong cùng một minor version của Python |
| Quản lý package | **uv** (`uv.lock` được commit) | Nhanh, chạy tốt trên Windows, lockfile tái lập được. Phương án dự phòng: `pip` + `venv` |
| Model / validate | pydantic >= 2.8 | Discriminated union, xuất JSON Schema |
| Test | pytest, hypothesis, pytest-cov, jsonschema | Mục 12 |
| Lint / format | ruff | Có thêm rule **TID251** để cấm các API làm mất tính tất định (mục 2.3) |
| Type check | mypy `strict` | Bắt lỗi kiểu dữ liệu trước khi chạy |
| CLI | `argparse` (thư viện chuẩn) | Không thêm dependency |

### 2.2. Cấu trúc thư mục

```
pundit-copilot/                      # gốc repo (thư mục hiện tại)
├─ claude.md · checklist.md · docs/day1-design.md
├─ .gitattributes                    # * text=auto eol=lf  (chống CRLF làm lệch golden hash)
├─ .github/workflows/ci.yml
├─ packages/schemas/                 # JSON Schema sinh tự động, KHÔNG sửa tay
├─ data/matches/                     # trận demo đã sinh (*.events.jsonl, *.meta.json)
└─ backend/
   ├─ pyproject.toml · uv.lock
   ├─ src/pundit/
   │  ├─ __init__.py · __main__.py · cli.py
   │  ├─ domain/                     # tầng thấp nhất, không import tầng nào khác
   │  │  ├─ pitch.py                 # kích thước sân, hệ toạ độ, flip, hình học
   │  │  ├─ thresholds.py            # ngưỡng cảnh báo tốc độ…
   │  │  ├─ matchtime.py             # phút, hiển thị "45+2'", Window
   │  │  ├─ ids.py                   # tạo / kiểm tra ID
   │  │  ├─ events.py                # Event schema (discriminated union)
   │  │  ├─ meta.py                  # MatchMeta, TeamSheet, PlayerInfo
   │  │  ├─ log.py                   # EventLog, LogView
   │  │  ├─ ballstate.py             # máy trạng thái bóng + PossessionInterval (dùng chung cho MatchState và stats)
   │  │  ├─ xmodels.py               # mô hình xG, xPass (dùng chung cho generator và stats)
   │  │  └─ errors.py
   │  ├─ data/rosters.json           # đội và cầu thủ hư cấu
   │  ├─ stats/                      # hàm thuần: (LogView, params) -> StatResult
   │  │  ├─ result.py · select.py · registry.py
   │  │  ├─ team.py · passing.py · possession.py   # P0
   │  │  └─ physical.py · tactical.py  # P1
   │  ├─ state/match_state.py        # ingest + validate + snapshot
   │  └─ engine/
   │     ├─ rng.py · generator.py · patterns.py · validate.py · replay.py · io.py
   │     └─ scenarios/base.py · scenarios/catalog.py
   └─ tests/                         # mục 12
```

**Quy tắc import một chiều** (kiểm tra khi review code): `domain` ← `stats` ← `state` ← `engine` ← `cli`. `stats` không được import `engine`.

### 2.3. Cấu hình chính (`backend/pyproject.toml`)

```toml
[project]
name = "pundit"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["pydantic>=2.8,<3"]

[project.scripts]
pundit = "pundit.cli:main"

[dependency-groups]
dev = ["pytest>=8", "pytest-cov>=5", "hypothesis>=6.100", "jsonschema>=4.22", "ruff>=0.6", "mypy>=1.11"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = ["slow: chạy nhiều seed (không chạy mặc định)", "perf: kiểm tra ngân sách thời gian"]

[tool.mypy]
strict = true
packages = ["pundit"]
mypy_path = "src"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF", "TID"]

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"random.random".msg  = "Dùng rng_stream(seed, name) — xem engine/rng.py"
"random.choice".msg  = "Dùng rng_stream(seed, name)"
"random.choices".msg = "Dùng rng_stream(seed, name)"
"random.randint".msg = "Dùng rng_stream(seed, name)"
"random.uniform".msg = "Dùng rng_stream(seed, name)"
"random.gauss".msg   = "Dùng rng_stream(seed, name)"
"random.shuffle".msg = "Dùng rng_stream(seed, name)"
"random.seed".msg    = "Không seed RNG toàn cục"
"uuid.uuid4".msg     = "ID phải tất định — xem domain/ids.py"
"datetime.datetime.now".msg = "Không dùng thời gian thực trong dữ liệu"
"time.time".msg      = "Dùng Clock (engine/replay.py)"
```

### 2.4. Các bẫy riêng của Windows (máy dev là Windows 11)

| Bẫy | Hậu quả | Cách xử lý |
|---|---|---|
| Encoding mặc định cp1252 | Tên tiếng Việt bị lỗi hoặc crash khi ghi file | Luôn `open(..., encoding="utf-8")` |
| Ghi file text sinh `\r\n` | Golden hash khác nhau giữa Windows và Linux | Ghi với `newline="\n"`, kèm `.gitattributes` `* text=auto eol=lf` |
| Console không in được dấu tiếng Việt | Chữ lỗi khi replay | Gọi `sys.stdout.reconfigure(encoding="utf-8")` trong `cli.main()`; không dùng emoji trong output console |
| Chuỗi Unicode dạng NFD / NFC | Hai tên trông giống nhau nhưng so sánh khác nhau | Roster lưu ở dạng **NFC**; có test kiểm tra |

---

## 3. Domain primitives

### 3.1. Sân và hệ toạ độ (`domain/pitch.py`)

```python
PITCH_LENGTH, PITCH_WIDTH = 105.0, 68.0
GOAL_X, GOAL_Y, GOAL_WIDTH = 105.0, 34.0, 7.32
BOX_X_MIN, BOX_Y_MIN, BOX_Y_MAX = 88.5, 13.84, 54.16      # vòng cấm: sâu 16.5 m, rộng 40.32 m
SIX_YARD_X_MIN = 99.5
PENALTY_SPOT = (94.0, 34.0)
FINAL_THIRD_X = 70.0
PPDA_ZONE_X = 42.0     # 60% sân phía trên, tính theo hệ toạ độ của đội đang pressing
```

**Quy ước bắt buộc: Attacking frame.** Toạ độ `(x, y)` của mỗi event nằm trong **hệ toạ độ của đội thực hiện event đó**, với đội này luôn tấn công về phía `x = 105`.

- `flip(x, y) = (105 - x, 68 - y)` (quay sân 180°) chuyển toạ độ sang hệ của đội kia. Tính chất: `flip(flip(p)) == p`.
- Nhờ quy ước này, mọi chỉ số tấn công dùng chung một ngưỡng (ví dụ final third là `x >= 70`) mà không phải xét đội nào đá bên nào.
- Muốn vẽ lên sân thật (Ngày 4) thì dùng hàm `to_broadcast(point, team, period, meta)`, dựa trên `meta.home_attacks_right_p1`. Hai đội đổi sân sau hiệp 1.

### 3.2. Thời gian (`domain/matchtime.py`)

Mỗi event có 3 trường thời gian (đều là số nguyên mili giây):

| Trường | Ý nghĩa | Ví dụ |
|---|---|---|
| `period` | Hiệp: 1 hoặc 2 | 2 |
| `clock_ms` | Thời gian đã trôi trong hiệp, **có thể vượt 45:00** khi bù giờ | 2 820 000 (47:00) |
| `match_ms` | Thời gian thi đấu cộng dồn, tăng đơn điệu. Hiệp 1: `= clock_ms`. Hiệp 2: `= độ dài hiệp 1 + clock_ms` | |

Hai trường hiển thị được **suy ra** từ trên và được validator kiểm tra cho khớp:
- `minute = (0 nếu hiệp 1, 45 nếu hiệp 2) + clock_ms // 60000`. Đây là phút **tính từ 0**: event lúc 0:30 có `minute = 0`.
- `second = (clock_ms // 1000) % 60`

```python
def format_minute(period: int, clock_ms: int) -> str:
    """Hiển thị kiểu bóng đá: 0:00–0:59 là phút 1'; 45:00 của hiệp 1 là 45+1'."""
    regular_end = 45 if period == 1 else 90
    shown = (0 if period == 1 else 45) + clock_ms // 60_000 + 1
    return f"{shown}'" if shown <= regular_end else f"{regular_end}+{shown - regular_end}'"

def minute_exact(period: int, clock_ms: int) -> float:
    return (0 if period == 1 else 45) + clock_ms / 60_000

def parse_display_minute(text: str) -> tuple[int, int]:
    """'27' -> (1, 1_560_000) ; "45+2'" -> (1, 2_760_000) ; "90+2'" -> (2, 2_760_000). Trả (period, clock_ms bắt đầu phút đó)."""
```

**Cửa sổ thời gian (Window).** Mọi cửa sổ là khoảng **nửa mở `[start, end)`**, để event nằm đúng biên không bị đếm hai lần.

```python
class MinuteWindow(BaseModel):      # theo phút bóng đá, dùng cho claim kiểu "hiệp 2", "từ phút 60"
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["minute"] = "minute"
    period: Literal[1, 2] | None = None
    start_min: float | None = None  # so với minute_exact, inclusive
    end_min: float | None = None    # exclusive

class RollingWindow(BaseModel):     # theo thời gian thi đấu thực, dùng cho "5 phút gần nhất"
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["rolling"] = "rolling"
    start_ms: int                   # inclusive, so với match_ms
    end_ms: int                     # exclusive

    @classmethod
    def last(cls, minutes: float, now_ms: int) -> "RollingWindow":
        return cls(start_ms=max(0, now_ms - round(minutes * 60_000)), end_ms=now_ms + 1)  # có gồm event tại now

Window = Annotated[MinuteWindow | RollingWindow, Field(discriminator="kind")]
FULL, H1, H2 = MinuteWindow(), MinuteWindow(period=1), MinuteWindow(period=2)
```

- `MinuteWindow(period=2, start_min=75)` nghĩa là "15 phút cuối", **có gồm bù giờ**.
- `MinuteWindow(start_min=40, end_min=50)` không chỉ định hiệp nên gồm cả bù giờ hiệp 1 lẫn phút 45–50 của hiệp 2. Hành vi này được ghi rõ và có test.
- Validator báo lỗi khi `start >= end` hoặc giá trị âm.
- **Giao giữa một khoảng thời gian và cửa sổ** (dùng cho kiểm soát bóng theo thời gian, mục 4.4): `overlap_ms(period, period_start_ms, start_ms, end_ms, window) -> int`. Với `MinuteWindow`, biên phút được đổi sang `match_ms` theo hiệp của khoảng đó: `period_start_ms + round((phút − offset) × 60 000)`, với `offset` là 0 (hiệp 1) hoặc 45 (hiệp 2). Khác hiệp với `window.period` thì kết quả là 0. Kết quả luôn `>= 0`.

### 3.3. ID (`domain/ids.py`)

| Loại | Định dạng | Ví dụ |
|---|---|---|
| Team | `[a-z0-9_]+` | `harbor_lions` |
| Player | `{team_id}-{shirt:02d}` | `harbor_lions-09` |
| Match | `{scenario}-{seed}` | `dominant_loser-42` |
| Event | `{match_id}-{seq:05d}` | `dominant_loser-42-00017` |

Mọi ID đều tất định. `seq` bắt đầu từ 0 và tăng liên tục, không có khoảng trống.

### 3.4. Mô hình xG và xPass (`domain/xmodels.py`)

Generator và stats **dùng chung** module này, nên dữ liệu sinh ra và số liệu đo được luôn nhất quán với nhau.

```python
def shot_angle(x: float, y: float) -> float:            # góc nhìn khung thành (radian)
    dx, dy = GOAL_X - x, abs(GOAL_Y - y)
    return math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2)

def xg(x: float, y: float, body_part: BodyPart, situation: Situation) -> float:
    if situation == "penalty":
        return 0.76
    logit = -1.0 + 1.9 * shot_angle(x, y) - 0.11 * math.hypot(GOAL_X - x, GOAL_Y - y)
    if body_part == "head":
        logit -= 0.9
    return round(min(0.95, max(0.01, _sigmoid(logit))), 3)
```

| Vị trí sút (giữa khung) | xG xấp xỉ |
|---|---|
| Cách khung 5 m, sút chân | 0.70 |
| Cách 5.5 m, đánh đầu | 0.43 |
| Cách 11 m (chấm phạt đền, tình huống thường) | 0.27 |
| Mép vòng cấm (16.5 m) | 0.12 |
| Cách 25 m | 0.04 |
| Phạt đền | 0.76 |

```python
def pass_completion_prob(x, y, end_x, end_y, *, under_pressure: bool, height: Height,
                         is_cross: bool, control: float = 0.5) -> float:
    length = math.hypot(end_x - x, end_y - y)
    progress = max(0.0, end_x - x)
    into_box = in_box(end_x, end_y) and not in_box(x, y)
    logit = (3.2 - 0.045 * length - 0.02 * progress - 0.9 * under_pressure - 0.6 * into_box
             - 0.5 * (height == "high") - 1.2 * is_cross + 2.4 * (control - 0.5))
    return min(0.99, max(0.01, _sigmoid(logit)))

def pass_difficulty(...) -> float:   # thuộc tính của đường chuyền, KHÔNG phụ thuộc trình độ đội
    return 1.0 - pass_completion_prob(..., control=0.5)

def difficulty_rating(d: float) -> float:   # thang 1–10 để hiển thị
    return round(1 + 9 * d, 1)
```

Ví dụ: chuyền ngắn 10 m không bị áp sát có độ khó ≈ 1.5/10. Chuyền dài 30 m lên phía trước khi bị pressing có độ khó ≈ 4.3/10. Tạt bóng bổng 35 m vào vòng cấm có độ khó ≈ 7.4/10.

Các hệ số trên **được phép tinh chỉnh**. Test kiểm tra các **tính chất** (đơn điệu, đối xứng, nằm trong biên) cùng vài giá trị mốc với sai số ±0.01. Khi đổi hệ số thì cập nhật golden theo quy trình ở mục 12.6.

---

## 4. Event schema (`domain/events.py`)

### 4.1. Trường chung (`EventBase`)

| Trường | Kiểu | Ràng buộc | Ghi chú |
|---|---|---|---|
| `id` | str | `^[a-z0-9_]+-\d+-\d{5}$` | |
| `match_id` | str | | |
| `seq` | int | `>= 0` | |
| `type` | Literal | discriminator | 18 loại (mục 4.2) |
| `period` | `1 \| 2` | | |
| `clock_ms` | int | `0..3 600 000` | |
| `match_ms` | int | `>= 0` | |
| `minute`, `second` | int | khớp với `period` và `clock_ms` | validator kiểm tra |
| `team` | TeamId \| None | | đội **thực hiện** event |
| `player` | PlayerId \| None | | cầu thủ thực hiện |
| `x`, `y` | float \| None | `0..105`, `0..68`, làm tròn 0.1 | trong attacking frame của `team` |
| `outcome` | Literal theo từng type \| None | | |
| `related_ids` | `tuple[str, ...]` | chỉ trỏ tới event có `seq` nhỏ hơn | |
| `possession_id` | int \| None | `>= 1` | |
| `possession_team` | TeamId \| None | | đội đang giữ bóng |

Cấu hình model: `ConfigDict(frozen=True, extra="forbid")`. Mỗi lớp con khai báo hai `ClassVar`: `REQUIRES_LOCATION` và `REQUIRES_POSSESSION`. Validator chung dựa vào đó để kiểm tra trường nào bắt buộc, trường nào phải là `None`.

### 4.2. Các loại event

| `type` | team / player | x, y | `outcome` | Trường riêng |
|---|---|---|---|---|
| `match_start` | — | — | — | — |
| `period_start` | — | — | — | — |
| `period_end` | — | — | — | — |
| `match_end` | — | — | — | — |
| `pass` | ✔ | ✔ | `complete` · `incomplete` · `out` · `offside` | `end_x`, `end_y`, `duration_ms > 0`, `height` (`ground`/`low`/`high`), `play_type` (`open_play`/`kick_off`/`free_kick`/`corner`/`throw_in`/`goal_kick`), `recipient`, `under_pressure`, `is_cross`, `is_through_ball` |
| `shot` | ✔ | ✔ | `goal` · `saved` · `off_target` · `blocked` · `post` | `xg`, `body_part` (`right_foot`/`left_foot`/`head`), `situation` (`open_play`/`counter`/`set_piece`/`corner`/`penalty`), `speed_kmh` (20–140), `goalkeeper` |
| `goal` | ✔ (người ghi bàn) | — | — | `assist`, `own_goal` (luôn `False` trong MVP); `related_ids = [shot_id]` |
| `pressure` | ✔ (đội phòng ngự) | ✔ | — | `target_player`, `duration_ms` |
| `tackle` | ✔ (đội phòng ngự) | ✔ | `won` · `lost` | `target_player` |
| `interception` | ✔ | ✔ | — | `related_ids = [pass_id bị cắt]` |
| `ball_recovery` | ✔ (đội giành bóng) | ✔ | — | Giành bóng tự do: thủ môn bắt bóng sau cú sút, nhặt bóng bật ra sau pha chặn hoặc dội cột. `related_ids = [shot_id]` |
| `foul` | ✔ (đội phạm lỗi) | ✔ | — | `fouled_player` |
| `card` | ✔ | — | — | `color` (`yellow`/`second_yellow`/`red`); `related_ids = [foul_id]` |
| `offside` | ✔ (đội tấn công) | ✔ | — | `via_var` (bool). `related_ids = [pass_id]`; khi `via_var=true` thì `related_ids = [pass_id, var_check_id]` |
| `ball_dead` | ✔ (đội vừa giữ bóng) | — | — | `reason` (`out_of_play`/`foul`/`offside`/`goal`/`period_end`); `related_ids = [event gây ra]` (trừ `period_end`) |
| `substitution` | ✔ (`player` = người rời sân) | — | — | `player_in` |
| `var_check` | — | — | — | `check_type` (`goal`/`red_card`/`penalty`), `decision` (`stands`/`overturned`), `reason` (`offside`/`foul`/`handball`/`none`), `duration_ms`; `related_ids = [event được kiểm tra]` |
| `player_physical` *(P1)* | ✔ | — | — | **Cộng dồn**: `distance_m`, `top_speed_kmh`, `sprints` |

**Các quyết định để tránh đếm trùng:**
- **Phạt góc** được biểu diễn bằng `pass` có `play_type="corner"`, không tách thành event riêng. Số quả phạt góc là số đường chuyền loại này.
- **Bàn thắng** là event `goal` riêng, trỏ về `shot` có `outcome="goal"`. Tỷ số **chỉ** được đếm từ event `goal` (trừ các bàn bị VAR huỷ, xem mục 7.2).
- Các trường suy ra được (độ dài đường chuyền, tốc độ bóng) **không lưu** trong event; stats tự tính khi cần.
- **Trạng thái bóng là dữ liệu tường minh:** bóng chết đúng lúc có `ball_dead` và lăn lại đúng lúc có pass restart. Thời gian kiểm soát bóng được tính từ các mốc này, không phải đoán (mục 4.4).

### 4.3. Union, parse và xuất schema

```python
Event = Annotated[
    MatchStartEvent | PeriodStartEvent | PeriodEndEvent | MatchEndEvent | PassEvent | ShotEvent
    | GoalEvent | PressureEvent | TackleEvent | InterceptionEvent | FoulEvent | CardEvent
    | OffsideEvent | BallRecoveryEvent | BallDeadEvent | SubstitutionEvent | VarCheckEvent
    | PlayerPhysicalEvent,
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)
# Đọc:  EVENT_ADAPTER.validate_json(line)
# Ghi:  EVENT_ADAPTER.dump_json(event)   → 1 dòng JSONL
# Schema: EVENT_ADAPTER.json_schema(mode="serialization") → packages/schemas/event.schema.json
```

**Validator theo từng loại** (lỗi phát hiện ngay khi tạo object):
- `pass`: nếu `outcome=complete` thì phải có `recipient` và `recipient != player`; ngược lại `recipient` phải là `None`. `end_x`, `end_y` nằm trong sân.
- `shot`: `0.01 <= xg <= 0.95`, trừ `situation=penalty` thì `xg == 0.76`.
- `goal` và `interception`: `related_ids` có đúng 1 phần tử.
- `card`: `color` hợp lệ. Quy tắc thẻ vàng thứ hai được kiểm tra ở `MatchState`, vì cần biết lịch sử trận.

Mọi event được tạo qua **một factory duy nhất** là `EventFactory` (thuộc engine, cũng được test builder dùng). Factory tự gán `id`, `seq`, `match_ms`, `minute`, `second`, nên không ai phải tự tính các trường này.

### 4.4. Trạng thái bóng và kiểm soát bóng theo thời gian (`domain/ballstate.py`)

Kiểm soát bóng được tính bằng **thời gian mỗi đội giữ bóng trên tổng thời gian bóng lăn**, không theo số đường chuyền. Thời gian bóng chết (đá phạt, ném biên, ăn mừng bàn thắng, VAR, thay người) **không tính cho đội nào**. Muốn tính chính xác thì log phải ghi rõ lúc nào bóng chết và lúc nào bóng lăn lại.

**Máy trạng thái:** tại mọi thời điểm, bóng ở một trong hai trạng thái: `DEAD` hoặc `IN_PLAY(đội X)`.

| Chuyển trạng thái | Event kích hoạt | Tác dụng |
|---|---|---|
| `DEAD → IN_PLAY(X)` | `pass` có `play_type != open_play` của đội X (kick-off, throw-in, goal kick, free kick, corner) | Mở possession mới cho X |
| `IN_PLAY(X) → IN_PLAY(Y)` | `interception`, `tackle(won)` hoặc `ball_recovery` của đội Y, với Y ≠ X | Đóng possession của X và mở possession của Y, **đúng thời điểm của event** |
| `IN_PLAY(X) → DEAD` | `ball_dead` | Đóng possession của X |

**Quy ước khi bóng đang bay:** bóng thuộc về đội chạm bóng cuối cùng cho tới khi đội kia giành được bóng hoặc bóng chết. Ví dụ: với đường chuyền bị cắt, thời gian bóng bay tới điểm cắt vẫn tính cho đội chuyền.

**Event được phép theo trạng thái:**
- Chỉ khi bóng đang lăn: pass open play, `shot`, `goal`, `pressure`, `tackle`, `interception`, `ball_recovery`, `foul`, `offside`, `ball_dead`.
- Chỉ khi bóng chết: pass restart, `match_start`, `period_start`, `period_end`, `match_end`.
- Lúc nào cũng được: `card`, `substitution`, `var_check`, `player_physical`.
- **Ngoại lệ duy nhất:** `offside` khi bóng chết được chấp nhận nếu `related_ids` có một `var_check` overturned (việt vị do VAR phát hiện sau bàn thắng).

```python
class PossessionInterval(BaseModel):          # frozen
    team: TeamId
    possession_id: int
    period: Literal[1, 2]
    period_start_ms: int                       # match_ms của period_start, dùng cho overlap_ms
    start_ms: int                              # match_ms, inclusive
    end_ms: int                                # match_ms, exclusive
    start_event_id: str                        # restart pass hoặc event giành bóng
    end_event_id: str | None                   # None = possession đang diễn ra (live)

class BallState(BaseModel):                    # frozen
    period: Literal[1, 2] | None
    period_start_ms: int
    holder: TeamId | None                      # None = bóng chết
    possession_id: int                         # 0 trước possession đầu tiên
    since_ms: int
    start_event_id: str | None

# domain/ballstate.py — NƠI DUY NHẤT chứa luật trạng thái bóng
def step(s: BallState, e: Event) -> tuple[BallState, PossessionInterval | None]:
    """Chuyển trạng thái theo 1 event; trả về interval vừa đóng (nếu có).
    Event sai luật → InvalidEventError (INV-14 hoặc INV-22)."""

# stats/possession.py
def possession_timeline(view: LogView) -> tuple[PossessionInterval, ...]:
    """Áp step() lần lượt qua mọi event. Nếu cuối view bóng vẫn đang lăn, interval cuối
    được đóng tại match_ms của event cuối cùng trong view (end_event_id=None)."""
```

- `MatchState.apply()` (để kiểm tra) và `stats.possession` (để tính) đều gọi chung `step()`, nên luật chỉ được viết một lần.
- **Mỗi `possession_id` ứng với đúng một interval.** `possession_id` tăng 1 tại mỗi event mở possession và không đổi ở bất kỳ chỗ nào khác.
- **Cập nhật live:** sau mỗi event, tỷ lệ kiểm soát bóng được tính lại tới thời điểm hiện tại. Giữa hai event, frontend có thể nội suy từ snapshot (mục 6.2 và 16).

---

## 5. Metadata và roster (`domain/meta.py`, `data/rosters.json`)

```python
class PlayerInfo(BaseModel):  # frozen, extra=forbid
    id: PlayerId; team_id: TeamId; shirt: int; name: str; short_name: str
    position: Literal["GK", "DF", "MF", "FW"]
    role: Literal["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
    foot: Literal["right", "left"]; nationality: str   # ISO alpha-2

class TeamInfo(BaseModel):
    id: TeamId; name: str; short_name: str   # 3 ký tự, ví dụ "HAR"
    primary_color: str; secondary_color: str  # "#RRGGBB" (dùng cho lower-third)
    formation: Literal["4-3-3", "4-2-3-1", "4-4-2", "3-5-2"]

class TeamSheet(BaseModel):
    team: TeamInfo; starting_xi: tuple[PlayerId, ...]; bench: tuple[PlayerId, ...]
    # validator: đúng 11 người đá chính, đúng 1 GK; dự bị <= 9; không trùng người

class MatchMeta(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    generator_version: str                    # ví dụ "0.1.0"
    match_id: str; scenario: str; seed: int; attempt: int
    synthetic: Literal[True] = True
    disclaimer: str = "Synthetic data. Fictional teams and players. Not affiliated with the Premier League."
    competition: str = "Synthetic League"     # không dùng tên giải thật
    venue: str; referee: str
    kickoff_utc: str                          # lấy từ kịch bản, KHÔNG dùng now()
    weather: Literal["clear", "cloudy", "rain", "wind"]
    home: TeamSheet; away: TeamSheet
    home_attacks_right_p1: bool
    players: dict[PlayerId, PlayerInfo]       # toàn bộ cầu thủ trong 2 TeamSheet
```

**Roster hư cấu:** 4 đội × 20 cầu thủ, ví dụ Harbor Lions FC (HAR), Kestrel Athletic (KES), Ironvale United (IRO), Saltmarsh Rovers (SAL).

- Tên đội và tên cầu thủ đều là tên tự đặt.
- **Không** dùng tên cầu thủ nổi tiếng có thật, kể cả cầu thủ Việt Nam.
- Mỗi đội có 1 cầu thủ người Việt **hư cấu**, phục vụ chế độ *Player focus* cho fan Việt (đã chốt).
- Toàn bộ tên được lưu ở dạng chuẩn hoá Unicode NFC.

---

## 6. EventLog, LogView và MatchState

### 6.1. EventLog và LogView (`domain/log.py`)

```python
class EventLog:                      # chỉ MatchState được ghi vào
    def append(self, e: Event) -> None
    def view(self, upto: int | None = None) -> LogView   # nhìn vào prefix [0, upto)

class LogView:                       # bất biến; luôn là prefix của log
    meta: MatchMeta
    n: int                           # số event nhìn thấy được
    def __iter__(self) -> Iterator[Event]
    def of_type(self, *types: str) -> Iterator[Event]   # dùng index theo type, lọc seq < n
    def get(self, event_id: str) -> Event               # KeyError nếu không có
    @property
    def last_seq(self) -> int                           # -1 nếu rỗng
    @classmethod
    def from_events(cls, meta: MatchMeta, events: Sequence[Event]) -> LogView  # dùng cho test / batch
```

Log chỉ được thêm vào cuối, nên một `LogView` tạo ra lúc `n=500` luôn thấy đúng 500 event đầu, kể cả khi log đã dài thêm. Nhờ vậy không phát sinh lỗi đọc dữ liệu đang được ghi dở.

### 6.2. MatchState (`state/match_state.py`)

```python
class MatchState:
    def __init__(self, meta: MatchMeta) -> None
    def apply(self, e: Event) -> None        # kiểm tra invariant tăng dần; vi phạm → raise InvalidEventError(code, event_id, detail)
    def view(self) -> LogView
    def snapshot(self) -> MatchSnapshot      # tỷ số, hiệp, phút hiển thị, cầu thủ trên sân, thẻ, event cuối,
                                             # trạng thái bóng và kiểm soát bóng live (xem bên dưới)
```

- `apply()` theo dõi trạng thái **cần cho việc kiểm tra**: cầu thủ đang trên sân, số thẻ, số lần thay người đã dùng, hiệp hiện tại, `seq` và `match_ms` cuối cùng, và trạng thái bóng (gọi `ballstate.step`).
- `snapshot()` **không** lưu số liệu thống kê riêng. Tỷ số và kiểm soát bóng được lấy bằng cách gọi `stats` trên `view()`, nên chỉ có một cách tính duy nhất.
- Các trường live của `MatchSnapshot`:
  - `as_of_ms`: `match_ms` của event cuối cùng.
  - `ball_in_play`, `holder_team`, `holder_since_ms`.
  - `possession_ms`: số ms giữ bóng của từng đội, **đã gồm đoạn đang diễn ra** tính tới `as_of_ms`.
  - `ball_in_play_ms`, `possession_pct`.
- **Bộ kiểm tra đầy đủ** (`engine/validate.py`) dùng lại `MatchState.apply()` cho từng event, rồi kiểm tra thêm các invariant toàn cục (mục 10). Luật chỉ được viết một lần.
- Mô hình chạy: chỉ một tác vụ ghi (replay hoặc ingest), nhiều nơi đọc qua `LogView`. Mọi thứ chạy trên một event loop asyncio nên không cần lock.

---

## 7. Stats Tools (`stats/`)

### 7.1. Kết quả trả về chuẩn (`stats/result.py`)

```python
class StatResult(BaseModel):               # frozen, extra=forbid
    metric: str
    team: TeamId | None
    player: PlayerId | None
    window: Window
    value: float | None
    unit: Literal["count", "percent", "xg", "meters", "kmh", "minutes", "ratio", "index", "rating"]
    numerator: float | None = None         # ví dụ possession: 1 062 000 ms đội giữ bóng
    denominator: float | None = None       # ví dụ: 1 690 000 ms bóng lăn của cả hai đội
    evidence_event_ids: tuple[str, ...]    # đã sắp theo seq, không trùng
    status: Literal["ok", "insufficient_data"]
    as_of_seq: int                         # seq cuối cùng trong view lúc tính
    definition: str                        # định nghĩa ngắn (tiếng Anh), dùng cho phần "Why"
    label: str | None = None               # nhãn diễn giải, ví dụ chaos_index: "control" / "balanced" / "chaos"
```

Validator kiểm tra các điều kiện sau:
- `status="ok"` thì `value` khác `None`; `status="insufficient_data"` thì `value` là `None`.
- Đơn vị `percent` thì `value` nằm trong `[0, 100]`.
- Đơn vị `count` thì `value == len(evidence_event_ids)`.

### 7.2. Lọc dữ liệu tập trung (`stats/select.py`)

```python
def voided_event_ids(view: LogView) -> frozenset[str]:
    """Bàn thắng bị VAR huỷ (var_check: check_type=goal, decision=overturned) → huỷ cả goal và shot của nó."""

def select(view: LogView, *, types: tuple[str, ...], team: TeamId | None = None,
           player: PlayerId | None = None, window: Window = FULL,
           include_voided: bool = False) -> list[Event]:
    """NƠI DUY NHẤT chứa logic lọc event. Mọi metric dạng đếm / trung bình đều gọi hàm này.
    Metric theo thời gian (possession…) dùng possession_timeline() + overlap_ms() (mục 4.4)."""
```

Định nghĩa đã chốt: **cú sút có bàn thắng bị VAR huỷ vì việt vị không được tính** vào số cú sút và xG, mà được tính là một lần việt vị. Quy tắc này nằm trong `voided_event_ids()` và có test riêng.

### 7.3. Danh mục chỉ số

Tham số chung là `MetricParams(team=None, player=None, window=FULL)`. Mỗi metric khai báo một `scope`:
- `team`: bắt buộc có `team`.
- `match`: không nhận `team`.
- `player`: bắt buộc có `player`.

Đội hoặc cầu thủ không tồn tại thì báo `UnknownTeamError` / `UnknownPlayerError`. Truyền tham số không khớp với `scope` thì báo `ValueError`.

**P0:**

| Metric | Định nghĩa | Unit | Evidence | Khi không có dữ liệu |
|---|---|---|---|---|
| `score` | Số `goal` của đội, trừ bàn bị huỷ | count | id các goal | 0 (ok) |
| `possession` | **Theo thời gian**: tổng thời gian đội giữ bóng / tổng thời gian bóng lăn × 100, tính trên các `PossessionInterval` giao với cửa sổ (mục 4.4). Thời gian bóng chết không tính. `numerator` và `denominator` tính bằng ms | percent | id event mở từng possession của đội | thời gian bóng lăn = 0 → insufficient |
| `passes` | Số pass của đội (mọi outcome) | count | id pass | 0 (ok) |
| `pass_accuracy` | pass `complete` / tổng pass × 100 | percent | id mọi pass đã thực hiện | tổng = 0 → insufficient |
| `shots` | Số shot không bị huỷ | count | id shot | 0 (ok) |
| `shots_on_target` | shot có outcome `goal` hoặc `saved` | count | id shot | 0 (ok) |
| `xg` | Tổng `xg` của các shot không bị huỷ | xg | id shot | 0.0 (ok) |
| `final_third_entries` | pass `complete` có `x < 70` và `end_x >= 70` | count | id pass | 0 (ok) |
| `corners` | pass có `play_type="corner"` | count | id pass | 0 (ok) |
| `fouls` · `cards` · `offsides` | Đếm event cùng loại (`cards` gồm cả thẻ vàng và đỏ) | count | id event | 0 (ok) |
| `pass_length_avg` | Trung bình `hypot(end − start)` | meters | id pass | không có pass → insufficient |
| `pass_difficulty_avg` | Trung bình `difficulty_rating` (thang 1–10) | rating | id pass | không có pass → insufficient |
| `difficult_passes_completed` | pass `complete` có `pass_difficulty >= 0.6` | count | id pass | 0 (ok) |

**P1:**

| Metric | Định nghĩa |
|---|---|
| `possession_time` | Thời gian đội giữ bóng, tính bằng phút (= `numerator` của `possession` / 60 000) |
| `ball_in_play_time` | *(scope `match`)* Tổng thời gian bóng lăn, tính bằng phút. Ngoại hạng Anh thực tế khoảng 55 phút/trận |
| `pass_share` | Tỷ lệ đường chuyền kiểu Opta (pass của đội / pass của cả hai đội × 100). **Chỉ dùng để đối chiếu**; chỉ số chính là `possession` |
| `pass_speed_avg` | Trung bình `length / duration` (km/h) của pass `complete` |
| `shot_speed_max` | Giá trị `speed_kmh` lớn nhất trong các shot |
| `box_entries` | pass `complete` kết thúc trong vòng cấm, bắt đầu ngoài vòng cấm |
| `player_distance` · `player_top_speed` | Lấy từ snapshot `player_physical` gần nhất (bắt buộc truyền `player`) |
| `speed_alerts` | Các snapshot mà `top_speed_kmh` vượt `SPEED_ALERT_KMH = 33.0` lần đầu, cộng các shot có `speed_kmh >= SHOT_SPEED_ALERT_KMH = 100.0` |
| `ppda` | Số pass của đối thủ trong 60% sân phía dưới của họ (`x <= 63` theo hệ toạ độ đối thủ) chia cho số pha phòng ngự của đội (tackle + interception + foul có `x >= 42` theo hệ toạ độ của đội). Mẫu số = 0 → insufficient. PPDA càng thấp nghĩa là pressing càng mạnh |
| `momentum` | Tỷ phần điểm đe doạ trong cửa sổ: shot `1 + 4·xg`, final-third entry `0.6`, box entry `1.0`, corner `0.5`, tackle thắng / interception / ball recovery ở nửa sân đối thủ `0.4`. Kết quả `(T − O) / (T + O)` nằm trong `[-1, 1]` (0 nếu không có điểm nào), dương nghĩa là nghiêng về `team`. Momentum "hiện tại" = cửa sổ `RollingWindow.last(10)`; biểu đồ dùng `momentum_series()` (cửa sổ trượt 10 phút, mỗi phút một điểm) |
| `chaos_index` | *(scope `match`)* Tính theo phút bóng lăn: `0.5·norm(số possession/phút, 3.0, 4.9) + 0.3·(1 − norm(số pass/possession, 3.6, 5.8)) + 0.2·norm((foul + tackle)/phút, 1.1, 2.3)`, kẹp trong `[0, 1]`. Các mốc là bách phân vị 10–90 của cửa sổ 10 phút trong trận `balanced`, nên một quãng cân bằng cho khoảng 0.5. Nhãn (`label`): `control` nếu < 0.4, `chaos` nếu > 0.6, còn lại `balanced` |
| `var_overturns` | Số bàn thắng của đội bị VAR huỷ (đếm tại thời điểm VAR quyết định) |
| `tempo` | Số pass mỗi phút khi đội giữ bóng (nhịp trận) |

Với `norm(v, lo, hi) = clamp((v − lo) / (hi − lo), 0, 1)`.

### 7.4. Registry (`stats/registry.py`)

```python
@metric(name="possession", unit="percent", scope="team", supports_player=False,
        description_en="Share of ball-in-play time in possession",
        description_vi="Tỷ lệ thời gian giữ bóng trên tổng thời gian bóng lăn")
def possession(view: LogView, p: MetricParams) -> StatResult: ...

REGISTRY: dict[str, MetricSpec]          # tên → (hàm, unit, mô tả, JSON schema của params)
def compute(view: LogView, name: str, params: MetricParams) -> StatResult   # UnknownMetricError nếu sai tên
def compare(view: LogView, name: str, window: Window) -> ComparisonResult   # cả hai đội + hiệu số + tỷ lệ
```

Registry phục vụ: kiểm tra expectation của kịch bản (mục 9), lệnh CLI `stats`, **tool cho agent** (Ngày 2) và **MCP server** (Ngày 6). Tất cả cùng một định nghĩa.

### 7.5. Hiệu năng

Một trận có khoảng 2 000 event, nên tính lại toàn bộ chỉ số mất chưa tới vài chục ms. **Ngày 1 không dùng cache**, để tránh lỗi dữ liệu cache bị cũ. Test `perf` (mục 12) giữ ngân sách: toàn bộ chỉ số P0 cho cả trận phải xong trong dưới 50 ms. Chỉ thêm cache (key gồm `view.n`) khi test này fail.

---

## 8. Synthetic Match Engine (`engine/`)

### 8.1. RNG tất định (`engine/rng.py`)

```python
def rng_stream(seed: int, attempt: int, name: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{attempt}:{name}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
```

Mỗi phần của generator có **luồng RNG riêng**: `flow` (diễn biến), `players` (chọn cầu thủ), `cards`, `subs`, `physical`, `patterns`, `meta`.

- Nhờ tách luồng, bật hoặc tắt `player_physical` không làm thay đổi diễn biến trận.
- Chỉ dùng các phương thức `random()`, `uniform()`, `randint()`, `choice()`, `choices()`, `gauss()` trên **list có thứ tự xác định**.
- Khi chọn từ một `set` (ví dụ cầu thủ đang trên sân) thì **luôn `sorted()` trước**.

### 8.2. Tham số đội theo từng giai đoạn

```python
class TeamProfile(BaseModel):          # mọi giá trị trong [0, 1]
    control: float     # độ chính xác chuyền → độ dài chuỗi giữ bóng → kiểm soát bóng
    press: float       # tần suất pressing / tackle → PPDA
    threat: float      # mức độ chuyền lên phía trước và tần suất sút
    discipline: float  # càng cao càng ít phạm lỗi và ít thẻ

class Segment(BaseModel):
    period: Literal[1, 2]
    from_minute: float | None = None   # None = từ đầu hiệp; giai đoạn có hiệu lực là giai đoạn cuối cùng đã bắt đầu
    home: TeamProfile; away: TeamProfile
```

### 8.3. Thuật toán chính

```
generate(scenario, seed) -> GeneratedMatch(meta, events)
  for attempt in 0..19:                                   # thử lại tất định (mục 8.7)
    rngs  = streams(seed, attempt)
    meta  = build_meta(scenario, rosters, rngs.meta)
    f     = EventFactory(meta)        # gán seq / id / minute / match_ms, chặn thời gian đi lùi
    sim   = SimState(meta)            # cầu thủ trên sân, dự bị, thẻ, thay người, đội giữ bóng, vị trí bóng
    emit match_start
    for period in (1, 2):
      end_ms = 45 phút + bù giờ ~ U(range của kịch bản)
      emit period_start ; kick_off (hiệp 1: đội nhà, hiệp 2: đội khách)
      while f.clock_ms < end_ms:
        thực hiện thay người hoặc thẻ đã đến hạn (khi bóng chết)
        if có scripted goal đến hạn: run_pattern(...)       # mục 8.5
        else: run_possession(sim, segment_hiện_tại)          # mục 8.4
        if qua mốc 5 phút: emit player_physical (luồng 'physical')   # P1
      if bóng đang lăn: emit ball_dead(reason=period_end)
      emit period_end
    emit match_end
    validate_all(meta, events)                  # INV-xx; vi phạm → raise (lỗi của generator, không thử lại)
    if expectations_ok(scenario, view): return  # dùng stats registry
  raise ScenarioUnsatisfiableError
```

### 8.4. Mô phỏng một chuỗi giữ bóng (`run_possession`)

Mỗi vòng lặp là một hành động của đội đang giữ bóng, tính trong attacking frame của đội đó:

| Quyết định | Xác suất / công thức |
|---|---|
| Có `pressure` trước hành động | `0.15 + 0.45 · opp.press · zone`, với `zone = 1.2` nếu `x < 35`, `1.0` nếu `35–70`, `0.8` nếu `> 70` |
| Có `tackle` khi đang bị pressure | `0.10 + 0.25 · opp.press`. Kết quả `won` 55% (mất bóng, mở possession mới cho đối thủ), `lost` 45% (tiếp tục) |
| Phạm `foul` (đối thủ phạm lỗi) | `0.012 · (1.6 − opp.discipline)`. Sau đó: `ball_dead(foul)` cùng thời điểm; thẻ vàng 15%, thẻ đỏ trực tiếp 0.5%; rồi đội bị phạm lỗi đá phạt |
| Sút | Trong vòng cấm: `0.10 + 0.25·threat`. Ở `70 ≤ x < 88.5`: `0.02 + 0.06·threat`. Còn lại: không sút |
| Điểm đến của đường chuyền | `end_x = x + N(−2 + 14·threat, 9)`, `end_y = y + N(0, 11)`, kẹp trong sân. Tạt bóng nếu `x > 80` và `y < 14` hoặc `y > 54` |
| Kết quả chuyền | Bernoulli với xác suất `pass_completion_prob(..., control=team.control)` |
| Chuyền hỏng | 65% bị `interception` tại một điểm trên đường bóng (đối thủ có possession mới). 35% bóng ra ngoài: `ball_dead(out_of_play)` lúc bóng chạm vạch, rồi đối thủ restart bằng `throw_in` / `goal_kick` / `corner` tuỳ vị trí |
| Việt vị | Chuyền lên phía trước có `end_x >= 80`: `0.05 + 0.05·threat`. Khi đó `pass.outcome=offside` → `offside` → `ball_dead(offside)` → đội kia đá phạt |
| Kết quả cú sút (`scripted_only`) | **Không bao giờ là bàn**: blocked 0.30 · off_target 0.36 · saved 0.30 · post 0.04 |
| Kết quả cú sút (`xg_sampled`) | Bàn thắng với xác suất `xg`; còn lại chia như trên |
| Sau cú sút (tại lúc bóng tới nơi) | `goal` → `goal` + `ball_dead(goal)` · `off_target` → `ball_dead(out_of_play)` → goal kick · `saved` → 65% thủ môn `ball_recovery` (giữ bóng rồi phát bóng open play), 35% `ball_dead(out_of_play)` → corner · `blocked` / `post` → 45% đối thủ `ball_recovery`, 25% `ball_dead(out_of_play)` → corner (blocked) hoặc goal kick (post), 30% đội tấn công tiếp tục, không có event giành bóng |

**Thời gian** (mỗi khoảng `U(a, b)` được lấy từ luồng RNG `flow`):

| Hành động | Thời lượng |
|---|---|
| Đường chuyền | `length / U(11, 20)` m/s; bóng bổng: `U(15, 24)` m/s |
| Giữ bóng trước hành động | `U(0.6, 3.0)` s |
| Throw-in · goal kick · free kick · corner | `U(8, 15)` · `U(15, 30)` · `U(20, 45)` · `U(25, 40)` s |
| Từ bàn thắng tới kick-off | `U(60, 90)` s |
| Thay người · VAR check | `U(30, 45)` · `U(60, 150)` s |
| Bóng bay của cú sút | `khoảng cách tới khung thành / (speed_kmh / 3.6)` |
| Thủ môn giữ bóng sau khi bắt | `U(3, 8)` s (tính vào thời gian kiểm soát bóng của đội thủ môn) |

Các khoảng throw-in, goal kick, free kick, corner, kick-off sau bàn thắng chính là **thời gian bóng chết**, tính từ `ball_dead` tới pass restart. Thay người và VAR làm khoảng này dài thêm. Với các giá trị trên, thời gian bóng lăn mỗi trận vào khoảng 55–60 phút, gần với Ngoại hạng Anh thực tế.

**Quy tắc possession** (generator phải tuân theo; INV-13, INV-14 và INV-22 kiểm tra):
- Possession chỉ đổi theo máy trạng thái ở mục 4.4: restart sau `ball_dead`, hoặc `interception` / `tackle(won)` / `ball_recovery` của đội **không** giữ bóng.
- `possession_team` của `pass`, `shot`, `goal`, `offside`, `interception`, `tackle(won)`, `ball_recovery` và `ball_dead` là chính đội đó.
- `possession_team` của `pressure`, `tackle(lost)` và `foul` là đội **đang giữ bóng**, tức là đối thủ của người thực hiện. MVP chỉ sinh lỗi do đội phòng ngự phạm.
- Mỗi lần bóng chết đều có `ball_dead` đúng thời điểm, nên thời gian bóng chết không bị cộng vào thời gian kiểm soát bóng.

### 8.5. Bàn thắng theo kịch bản (`engine/patterns.py`)

Mỗi pattern sinh ra một chuỗi event hợp lệ, với toạ độ và xG hợp lý:

| Pattern | Chuỗi event |
|---|---|
| `counter_attack` | `interception` ở `x ∈ [25, 40]` → 2–3 đường chuyền tiến lên (có một `is_through_ball`) → shot trong vòng cấm (`x ∈ [92, 99]`) → `goal` (assist là người chuyền cuối) |
| `cutback` | Đẩy bóng ra biên → chuyền tới gần đường biên ngang (`x ∈ [100, 103]`) → chuyền ngược vào `x ∈ [92, 95]` → shot → `goal` |
| `header_from_corner` | Cú sút bị chặn ra ngoài → `ball_dead(out_of_play)` → pass `play_type=corner`, `is_cross`, `height=high` → shot `body_part=head` ở `x ∈ [95, 99]` → `goal` |
| `long_shot` | Shot từ `x ∈ [78, 84]` (xG thấp, câu chuyện "siêu phẩm") → `goal` |
| *(cờ)* `var_overturn` | Sau `goal` + `ball_dead(goal)`: `var_check(goal, overturned, offside)` → `offside` lúc bóng chết (người ghi bàn; `related_ids` = [đường chuyền cuối, `var_check`]) → đội phòng ngự đá phạt. **Không có kick-off** |

Các quy tắc chung cho pattern:
- Mọi pattern ghi bàn đều kết thúc bằng `goal` rồi `ball_dead(goal)` cùng thời điểm.
- Pattern chỉ bắt đầu khi bóng đang lăn. Nếu tới hạn mà bóng đang chết, generator chờ tới restart kế tiếp.
- Nếu pattern cần đội ghi bàn đang giữ bóng (`cutback`, `long_shot`, `header_from_corner`) mà đối thủ đang có bóng, generator chèn một `tackle(won)` trước.
- Bàn thắng xuất hiện trong khoảng ±60 giây quanh phút đã định trong kịch bản.

### 8.6. Thay người, thẻ phạt, thể lực

- **Thay người:** mỗi đội 3 lần, lịch thay rơi vào khoảng `U(58, 84)`', thực hiện ở lần bóng chết đầu tiên sau mốc đó. Người rời sân là cầu thủ ngẫu nhiên không phải thủ môn (chọn từ list đã `sorted`). Người vào sân là dự bị cùng nhóm vị trí nếu có.
- **Thẻ:** thẻ vàng thứ hai chuyển thành `second_yellow` và cầu thủ bị đuổi. Thẻ đỏ theo kịch bản được gán vào pha phạm lỗi đầu tiên của đội đó sau mốc thời gian đã định.
- **`player_physical` (P1):**
  - Quãng chạy mỗi phút theo vai trò (m/phút): GK 60 · CB 105 · FB 115 · DM 120 · CM 125 · AM 118 · W 112 · ST 108, nhân `(0.9 + 0.2·press) · U(0.95, 1.05)`.
  - Mỗi 5 phút cộng thêm `randint(0, 3)` lần nước rút; tốc độ tối đa khi nước rút `U(28, 35.5)` km/h.
- **Tốc độ cú sút:** sút chân `U(70, 115) − 0.8·max(0, dist − 11)`, kẹp trong `[45, 130]`; đánh đầu `U(35, 60)` km/h.

### 8.7. Thử lại tất định

Nếu trận sinh ra không thoả expectation của kịch bản, generator thử lại với `attempt + 1`. Cùng `seed` luôn đi qua cùng chuỗi attempt, nên **kết quả vẫn tất định**. `meta.attempt` ghi lại attempt đã thành công.

Test bắt buộc: các seed 0–19 của mọi kịch bản đều thành công. Trung bình số attempt **≤ 3**; vượt mức này nghĩa là cần chỉnh tham số kịch bản. Seed demo (42) nên thành công ngay ở attempt 0.

---

## 9. Kịch bản (`engine/scenarios/`)

```python
class ScriptedGoal(BaseModel):
    side: Literal["home", "away"]; at: str            # "27'", "45+2'", "90+2'"
    pattern: Literal["counter_attack", "cutback", "header_from_corner", "long_shot"]
    var_overturn: bool = False

class ScriptedCard(BaseModel):
    side: Literal["home", "away"]; at: str; color: Literal["red"] = "red"

class Scenario(BaseModel):
    key: str; title_vi: str; title_en: str
    home: TeamId; away: TeamId; kickoff_utc: str; weather: str
    stoppage_minutes: dict[Literal[1, 2], tuple[int, int]]
    segments: tuple[Segment, ...]
    goal_mode: Literal["scripted_only", "xg_sampled"]
    scripted_goals: tuple[ScriptedGoal, ...] = (); scripted_cards: tuple[ScriptedCard, ...] = ()
    expectations: tuple[Expectation, ...] = ()
```

Validator chạy **ngay lúc import** catalog, nên lỗi gõ sai được phát hiện sớm:
- Mỗi hiệp có giai đoạn bắt đầu từ đầu hiệp.
- Phút trong `at` hợp lệ, và phút bù giờ được dùng không vượt quá mức tối thiểu của `stoppage_minutes`.
- Danh sách sự kiện kịch bản sắp theo thời gian.
- Expectation chỉ dùng metric có trong registry.

| Key | Ưu tiên | Câu chuyện | Kịch bản | Expectations (kiểm tra bằng registry) |
|---|---|---|---|---|
| `dominant_loser` | **P0** | Đội nhà (HAR) áp đảo hiệp 2 nhưng thua 1–2 | Hiệp 1: hai đội cân bằng (control 0.55 / 0.50). Hiệp 2: HAR control 0.75, press 0.75, threat 0.8; KES 0.35 / 0.3 / 0.25. Bàn thắng: KES 27' `header_from_corner`, HAR 63' `cutback`, KES 78' `counter_attack` | KES thắng ≥ 1 bàn · `possession(HAR, H2) ≥ 60` (theo thời gian bóng lăn) · `shots(HAR, H2) ≥ 8` và gấp ≥ 2 lần KES · `xg(HAR, H2) − xg(KES, H2) ≥ 0.8` · `xg(HAR, FULL) > xg(KES, FULL)` |
| `comeback_var` | P1 | IRO bị dẫn 0–2 rồi thắng 3–2; một bàn của SAL bị VAR huỷ vì việt vị | Bàn thắng: SAL 18' `long_shot`, SAL 35' `counter_attack` + `var_overturn`, SAL 52' `cutback`, IRO 68' `header_from_corner`, IRO 81' `cutback`, IRO 90+2' `counter_attack`. Bù giờ hiệp 2: `(4, 6)` | Tại 60' SAL dẫn 2 bàn · IRO thắng chung cuộc · có ≥ 1 `var_check` overturned · `offsides(SAL) ≥ 1` · `xg(IRO, H2) > xg(SAL, H2)` |
| `red_card_swing` | P2 | KES nhận thẻ đỏ 52', momentum đổi chiều, HAR thắng 2–1 | KES 30' ghi bàn; KES thẻ đỏ 52'; HAR 63', 77' ghi bàn. Giai đoạn từ 52': HAR threat cao | `momentum(HAR)` trung bình trước 52' < 0, sau 52' > 0 · HAR thắng |
| `balanced` | (test) | Trận ngẫu nhiên, hai đội ngang nhau | `goal_mode="xg_sampled"`, không có sự kiện kịch bản | Không có. Dùng cho test độ thực tế (mục 12.5) |

---

## 10. Invariants (`engine/validate.py`)

Mỗi luật có mã riêng. `InvalidEventError.code` mang mã đó, nên test có thể khẳng định **đúng luật nào** bị vi phạm.

| Mã | Luật |
|---|---|
| INV-01 | `seq` bắt đầu từ 0, liên tục; `id == f"{match_id}-{seq:05d}"`; id không trùng |
| INV-02 | Thứ tự: `match_start` → `period_start(1)` → … → `period_end(1)` → `period_start(2)` → … → `period_end(2)` → `match_end`. Không có event thi đấu nằm ngoài hiệp |
| INV-03 | `match_ms` không giảm; `clock_ms` không giảm trong cùng hiệp; `match_ms` khớp công thức ở mục 3.2 |
| INV-04 | `minute` và `second` khớp với `period` và `clock_ms` |
| INV-05 | `related_ids` chỉ trỏ tới event đã có, với `seq` nhỏ hơn, đúng loại theo bảng 4.2 |
| INV-06 | Mọi toạ độ (kể cả `end_x`, `end_y`) nằm trong `[0, 105] × [0, 68]` |
| INV-07 | Tốc độ ngầm định của đường chuyền (`length / duration`) nằm trong `[3, 40]` m/s |
| INV-08 | `player` thuộc `team`; `recipient` cùng đội và khác người chuyền; `target_player` và `fouled_player` thuộc đội đối thủ |
| INV-09 | Người thực hiện event đang có mặt trên sân (đá chính hoặc đã vào sân, chưa bị thay ra, chưa bị đuổi) |
| INV-10 | Thay người: người ra đang trên sân; người vào đang ở băng ghế và chưa từng vào sân; mỗi đội tối đa 5 lần |
| INV-11 | Thẻ: `second_yellow` chỉ sau một thẻ `yellow`; nhận `red` hoặc `second_yellow` thì phải rời sân; mỗi đội luôn còn ít nhất 7 người |
| INV-12 | Khi giao bóng mỗi đội có đúng 11 người; quân số chỉ giảm do thẻ đỏ |
| INV-13 | `possession_team` đúng theo quy tắc ở mục 8.4 |
| INV-14 | `possession_id` tăng đúng 1 tại mỗi event mở possession (restart, `interception`, `tackle(won)`, `ball_recovery`) và không đổi ở bất kỳ chỗ nào khác; trong cùng một possession thì `possession_team` không đổi |
| INV-15 | Sau một pass `complete`, event chạm bóng tiếp theo trong cùng possession phải do `recipient` thực hiện |
| INV-16 | Mỗi shot có `outcome=goal` có đúng 1 `goal` sau nó, cùng đội, và `goal` được theo ngay bởi `ball_dead(goal)` cùng `match_ms`; mỗi `goal` trỏ về đúng 1 shot như vậy |
| INV-17 | Restart phải đúng loại và đúng đội theo lần bóng chết trước đó: `kick_off` chỉ ở đầu hiệp (hiệp 1: đội nhà, hiệp 2: đội khách) hoặc sau bàn thắng **không bị huỷ** (đội thủng lưới); sau `foul` / `offside` / bàn bị VAR huỷ là `free_kick` của đội kia; sau `out_of_play` là `throw_in`, `goal_kick` hoặc `corner` |
| INV-18 | `xg` của shot bằng đúng `xmodels.xg(...)`; `speed_kmh` hợp lý theo `body_part` |
| INV-19 | `var_check` loại goal trỏ về một `goal` trước nó. Bàn bị `overturned` không có kick-off và không được tính tỷ số |
| INV-20 *(P1)* | `player_physical`: quãng chạy và tốc độ cao nhất không giảm; `top_speed ≤ 37` km/h; quãng chạy ≤ 14 km/90 phút; chỉ áp dụng cho cầu thủ đang trên sân |
| INV-21 | Mọi tên lấy từ roster; tên ở dạng NFC; `meta.synthetic is True` |
| INV-22 | **Máy trạng thái bóng** (mục 4.4): restart chỉ khi bóng chết; `ball_dead` và các event chỉ-khi-bóng-lăn chỉ xảy ra khi bóng đang lăn; event giành bóng phải do đội **không** giữ bóng thực hiện; `period_end` chỉ khi bóng chết. Ngoại lệ duy nhất: `offside` do VAR phát hiện |
| INV-23 | Các `PossessionInterval` không chồng lên nhau và nằm trọn trong hiệp của chúng; với dữ liệu do generator sinh thì mỗi interval dài > 0. Mỗi đoạn bóng chết bắt đầu bằng `ball_dead` (hoặc `period_start`) và kết thúc bằng một restart (hoặc `period_end`) |

---

## 11. Replay và CLI

### 11.1. Replay (`engine/replay.py`)

```python
class Clock(Protocol):
    def now(self) -> float: ...                       # giây, đơn điệu
    async def sleep(self, seconds: float) -> None: ...

class RealClock:   now = time.monotonic ; sleep = asyncio.sleep
class FakeClock:   # dùng cho test: thời gian chỉ trôi khi gọi sleep, có ghi lại các lần sleep
    def __init__(self) -> None: self.t = 0.0; self.sleeps: list[float] = []
    def now(self) -> float: return self.t
    async def sleep(self, s: float) -> None: self.sleeps.append(s); self.t += s

@dataclass(frozen=True)
class ReplayItem:
    event: Event
    catching_up: bool          # True = đang tua nhanh, người dùng chưa xem tới đoạn này

async def replay(events: Sequence[Event], *, speed: float, clock: Clock,
                 from_minute: float = 0.0, halftime_pause_s: float = 2.0) -> AsyncIterator[ReplayItem]:
    if speed <= 0:
        raise ValueError("speed must be > 0")
    live = started = False
    anchor_wall, anchor_ms, pause = 0.0, 0, 0.0
    for e in events:
        live = live or minute_exact(e.period, e.clock_ms) >= from_minute   # đã bật thì giữ luôn
        if not live:
            yield ReplayItem(e, catching_up=True)
            continue
        if not started:
            started, anchor_wall, anchor_ms = True, clock.now(), e.match_ms
        target = anchor_wall + (e.match_ms - anchor_ms) / 1000 / speed + pause
        delay = target - clock.now()                 # lịch tuyệt đối → không trôi lệch
        if delay > 0:
            await clock.sleep(delay)
        yield ReplayItem(e, catching_up=False)
        if e.type == "period_end" and e.period == 1:
            pause += halftime_pause_s
```

**Ba điểm quan trọng:**
1. Replay **trả về mọi event**, kể cả các event bị tua qua (gắn cờ `catching_up=True`). Nhờ vậy `MatchState` vẫn nhận đủ dữ liệu, và tỷ số khi nhảy tới phút 60 vẫn đúng.
2. Thời điểm phát được tính theo **lịch tuyệt đối**. Nếu bên nhận xử lý chậm, replay chỉ không ngủ nữa chứ không cộng dồn độ trễ.
3. `from_minute` dùng **chốt một chiều**: đã bắt đầu phát thì không quay lại chế độ tua, kể cả khi gặp phút bù giờ.

### 11.2. CLI (`python -m pundit` hoặc `pundit`)

| Lệnh | Chức năng |
|---|---|
| `generate --scenario S --seed N [--out data/matches] [--print-hash] [--update-golden]` | Ghi `{match_id}.events.jsonl` (UTF-8, LF) và `.meta.json`; in sha256 |
| `replay (--scenario S --seed N \| --file F) [--speed 20] [--from-minute 60] [--status-every 5]` | In mỗi event một dòng, ví dụ `[ 63'] HAR  GOAL  T. Calder (assist M. Ortega)  1-1`. Cứ mỗi 5 phút trận đấu, in thêm một dòng trạng thái tính live: `[ 65'] POSSESSION HAR 58% - KES 42% \| BALL IN PLAY 38:12` |
| `stats (--scenario S --seed N \| --file F) [--window FULL\|H1\|H2\|60-75]` | Bảng chỉ số P0 của hai đội, gồm kiểm soát bóng theo thời gian và tổng thời gian bóng lăn |
| `validate --file F` | Chạy toàn bộ INV-xx trên một file JSONL |
| `schema [--out packages/schemas]` | Xuất JSON Schema cho `Event`, `MatchMeta`, `StatResult` |

Mã thoát: `0` nếu thành công; `1` nếu dữ liệu không hợp lệ (in mã INV); `2` nếu sai tham số.

---

## 12. Chiến lược test

### 12.1. Các tầng test

| Tầng | Thư mục | Kiểm tra gì | Số lượng ước tính |
|---|---|---|---|
| Unit | `tests/unit/` | pitch, matchtime, ids, xmodels, events, từng metric với dữ liệu viết tay | ~60 |
| Contract | `tests/contract/` | JSON Schema đã commit khớp với schema sinh ra; mọi event sinh ra đều hợp lệ theo JSON Schema | ~5 |
| Property / metamorphic | `tests/property/` | Invariant trên nhiều seed; các tính chất của stats; tính tất định | ~25 |
| Scenario | `tests/scenario/` | Expectation của từng kịch bản; độ thực tế của trận `balanced` | ~10 |
| Replay | `tests/replay/` | Nhịp phát (FakeClock), tua nhanh, nghỉ giữa hiệp, huỷ giữa chừng | ~8 |
| Golden | `tests/golden/` | Hash và tóm tắt số liệu của các trận demo | ~3 |
| CLI smoke | `tests/cli/` | Các lệnh chạy được, mã thoát, output UTF-8 | ~5 |

Mục tiêu: toàn bộ chạy **dưới 60 giây**. Các test đánh dấu `slow` (200 seed) chỉ chạy khi cần (`pytest -m slow`). Coverage ≥ 85% toàn bộ, ≥ 95% cho `domain/` và `stats/`.

### 12.2. Test builder: dựng dữ liệu viết tay luôn hợp lệ

```python
def test_possession_is_share_of_ball_in_play_time():
    m = MatchBuilder(home="harbor_lions", away="kestrel_athletic")   # tự tạo meta, match_start, period_start(1)
    ko = m.at("0:00").kick_off("H9", to="H8")       # "H9" = đội nhà số áo 9; restart → HAR giữ bóng từ 0:00
    m.at("0:04").pass_("H8", to="H6")
    m.at("0:12").pass_("H6", outcome="incomplete")
    m.at("0:15").interception("A4")                  # KES giành bóng → HAR giữ bóng 15 s
    m.at("0:18").pass_("A4", outcome="out")
    m.at("0:20").ball_dead("out_of_play")            # bóng chết → KES giữ bóng 5 s
    ti = m.at("0:30").throw_in("H3", to="H7")        # HAR restart → HAR giữ bóng từ 0:30
    m.at("0:40").pass_("H7", to="H9")                # view dừng ở 0:40 → đoạn đang diễn ra: HAR thêm 10 s
    view = m.view()                                  # mọi event đã qua MatchState.apply()

    r = compute(view, "possession", MetricParams(team="harbor_lions"))
    assert (r.numerator, r.denominator) == (25_000, 30_000)    # ms; 10 s bóng chết không tính cho đội nào
    assert r.value == pytest.approx(250 / 3)                   # 83.33%
    assert r.evidence_event_ids == (ko.id, ti.id)              # các event mở possession của HAR
```

Builder dùng chung `EventFactory` và `MatchState` với code thật. **Fixture không hợp lệ sẽ fail ngay khi dựng**, nên không có chuyện test sai mà vẫn xanh.

### 12.3. Unit test: các ca bắt buộc

| Module | Ca kiểm thử |
|---|---|
| `matchtime` | `format_minute`: 0 ms → `1'`; 44:59.999 → `45'`; 45:00.000 (hiệp 1) → `45+1'`; 0 ms (hiệp 2) → `46'`; 45:00.000 (hiệp 2) → `90+1'`. `parse_display_minute` là phép nghịch đảo. **Biên cửa sổ**: event đúng `start` được tính, đúng `end` không được tính. `RollingWindow.last` có gồm event tại `now`. Window không hợp lệ thì báo lỗi. `overlap_ms`: khoảng cắt ngang biên cửa sổ được tính đúng số ms; khoảng nằm ngoài cửa sổ hoặc khác hiệp → 0 |
| `pitch` | `flip(flip(p)) == p`; `in_box` tại các biên 88.5 / 13.84 / 54.16; `to_broadcast` đổi chiều giữa hai hiệp |
| `xmodels` | Các giá trị mốc ±0.01 (bảng mục 3.4); penalty = 0.76; luôn nằm trong `[0.01, 0.95]` |
| `events` | Mỗi type: một mẫu hợp lệ parse được; thiếu trường bắt buộc thì lỗi; thừa trường thì lỗi (`extra=forbid`); `complete` mà thiếu `recipient` thì lỗi; round-trip `dump_json` → `validate_json` cho ra object bằng nhau |
| `meta` | 12 người đá chính thì lỗi; 0 hoặc 2 thủ môn thì lỗi; trùng cầu thủ thì lỗi; tên không ở dạng NFC thì lỗi |
| `stats` (mỗi metric) | Ca thường (giá trị tính tay) · ca rỗng (insufficient hoặc 0 đúng như bảng 7.3) · đội không tồn tại thì lỗi · lọc theo cửa sổ · evidence đúng các id |
| `select` / VAR | Bàn bị huỷ không được tính tỷ số, shot và xG, nhưng offside thì được tính. Bàn có `decision=stands` vẫn được tính |
| `ballstate` / possession | Mỗi chuyển trạng thái hợp lệ ở bảng 4.4 cho ra đúng interval. Mỗi chuyển trạng thái **sai** ra đúng mã lỗi: restart khi bóng đang lăn, `ball_dead` khi bóng đã chết, đội đang giữ bóng lại "giành" bóng, chuyền open play khi bóng chết (INV-22 / INV-14). `offside` do VAR lúc bóng chết được chấp nhận; `offside` lúc bóng chết mà không có `var_check` thì bị từ chối. View dừng giữa một possession → interval đóng tại event cuối. Thời gian bóng chết không được cộng cho đội nào |
| `MatchState` | Mỗi INV có ít nhất **1 test âm**: dựng event vi phạm rồi khẳng định `InvalidEventError.code == "INV-xx"`. Ví dụ: cầu thủ đã bị thay ra vẫn chuyền bóng → INV-09 |

### 12.4. Property và metamorphic test

Dùng **các seed của generator** làm đầu vào ngẫu nhiên (mặc định 20 seed × 4 kịch bản). Dùng **Hypothesis** cho các hàm thuần (hình học, xG, xPass, thời gian).

| # | Tính chất | Bug mà nó bắt được |
|---|---|---|
| M1 | Với mọi cửa sổ có thời gian bóng lăn > 0: `possession(home) + possession(away) == 100` | Lọc sai đội, bỏ sót interval |
| M2 | **Tính cộng**: `shots(H1) + shots(H2) == shots(FULL)`; tương tự cho mọi metric đếm, và cho `[0, 30) + [30, 60) + [60, ∞)` | Lỗi off-by-one ở biên cửa sổ |
| M3 | **Đơn điệu**: cửa sổ A chứa cửa sổ B thì `count(A) >= count(B)` | Logic lọc sai |
| M4 | **Evidence nhất quán**: mọi id trong evidence tồn tại và đúng đội. Với metric đếm: event nằm trong cửa sổ và `value == len(evidence)`. Với `possession`: evidence là event mở các interval **có giao** với cửa sổ (event này có thể nằm trước biên cửa sổ) | Số liệu không có bằng chứng (rất quan trọng cho Claim Check) |
| M5 | **Đổi vai hai đội**: hoán đổi nhãn đội trong log thì kết quả hoán đổi theo | Code ngầm định đội nhà |
| M6 | **Tăng dần bằng tính lại từ đầu**: `MatchState` áp từng event một cho ra snapshot bằng việc dựng lại từ log đầy đủ, tại mọi prefix | Lỗi trạng thái tích luỹ |
| M7 | **Prefix**: chỉ số tính trên `view(upto=n)` bằng chỉ số tính trên `LogView.from_events(events[:n])` | Rò rỉ dữ liệu "tương lai" |
| M8 | xG tăng khi tiến gần khung thành (trên trục giữa); đối xứng quanh `y = 34`; đánh đầu < sút chân | Sai công thức |
| M9 | Độ khó đường chuyền tăng theo độ dài; bị pressure thì khó hơn; `control` không ảnh hưởng `pass_difficulty` | Trộn trình độ đội vào thuộc tính của đường chuyền |
| M10 | Mọi INV-xx đúng trên mọi trận sinh ra | Generator sinh dữ liệu vô lý |
| M11 | **Thời gian cộng được**: `numerator` (ms) của `possession` ở H1 + H2 = FULL; tương tự với `[0, 30) + [30, 60) + [60, ∞)` | Cắt interval theo biên cửa sổ bị sai |
| M12 | **Bảo toàn thời gian**: trong mỗi hiệp, tổng thời gian bóng lăn + tổng thời gian bóng chết = độ dài hiệp | Bỏ sót hoặc đếm trùng một đoạn thời gian |
| M13 | **Đối chiếu chéo** (cần `pass_share`, P1): ở `dominant_loser` H2, cả `possession` lẫn `pass_share` của HAR đều ≥ 55 | Generator hoặc công thức lệch khỏi thực tế |

**Tính tất định:**
- **D1:** cùng seed sinh hai lần trong cùng process cho ra bytes JSONL giống hệt.
- **D2:** chạy `pundit generate` bằng **subprocess** với `PYTHONHASHSEED` = 0, 1, 12345 cho ra cùng sha256 (bắt lỗi duyệt `set`).
- **D3:** seed khác nhau thì trận khác nhau.
- **D4:** bật hoặc tắt `player_physical` không làm đổi các event còn lại.

Cấu hình Hypothesis (`conftest.py`): profile `ci` với `derandomize=True, deadline=None` để CI không fail ngẫu nhiên; chọn profile qua biến môi trường `HYPOTHESIS_PROFILE`.

### 12.5. Test kịch bản và độ thực tế

- **Kịch bản:** mỗi kịch bản với seed 0–19 đều thoả expectation; trung bình attempt ≤ 3; bàn thắng xuất hiện trong khoảng ±1 phút quanh phút đã định.
- **Độ thực tế** (`balanced`, 30 seed, so sánh **trung vị mỗi đội**): pass 350–600 · độ chính xác chuyền 72–90% · cú sút 7–18 · trúng đích 2–7 · xG 0.6–2.5 · phạm lỗi 7–15 · phạt góc 2–8 · việt vị 0–4 · thẻ vàng 0–4 · kiểm soát bóng 35–65% · tổng event (không tính `player_physical`) 1 500–2 800. Với cả trận: **thời gian bóng lăn 50–65 phút** (Ngoại hạng Anh thực tế khoảng 55 phút).
- **Hiệu năng** (`perf`): sinh 1 trận dưới 2 giây; toàn bộ chỉ số P0 cho cả trận dưới 50 ms.

### 12.6. Golden test và quy trình cập nhật

- Lưu `tests/golden/{match_id}.sha256` (hash file JSONL) và `{match_id}.summary.json` (tỷ số, số event, chỉ số P0 của từng hiệp) cho `dominant_loser-42` và `comeback_var-7`.
- Khi **cố ý** sửa generator: chạy `uv run pundit generate --scenario S --seed N --update-golden`, **đọc diff của `summary.json`** để chắc rằng thay đổi đúng như ý, rồi commit cùng lúc với code. Sửa generator mà không cập nhật golden thì CI fail.

### 12.7. CI (`.github/workflows/ci.yml`)

```yaml
name: ci
on: [push, pull_request]
jobs:
  backend:
    strategy: { matrix: { os: [ubuntu-latest, windows-latest] } }
    runs-on: ${{ matrix.os }}
    defaults: { run: { working-directory: backend } }
    env: { HYPOTHESIS_PROFILE: ci }
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with: { python-version: "3.12" }
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run pytest --cov=pundit --cov-fail-under=85
```

Chạy trên **Windows** giúp bắt các lỗi encoding và CRLF mà máy dev có thể bỏ sót. Chạy trên **Ubuntu** để chắc rằng sau này deploy bằng container Linux (Ngày 5) vẫn ra cùng kết quả.

### 12.8. Nghiệm thu thủ công cuối ngày (15 phút)

1. `pundit replay --scenario dominant_loser --seed 42 --speed 20 --from-minute 40`: xem có cảm giác HAR áp đảo hiệp 2 không, và tên tiếng Việt có hiển thị đúng trong PowerShell không.
2. `pundit stats ... --window H2`: các số liệu đủ để kết luận "Đội nhà áp đảo hiệp 2 nhưng vẫn thua". Ghi lại các con số này để dùng trong video demo.
3. Mở file JSONL, kiểm tra ngẫu nhiên 10 event: toạ độ, tên, thời gian có hợp lý không.
4. Chạy `pundit validate --file data/matches/dominant_loser-42.events.jsonl` → mã thoát 0.
5. Tick checklist; đưa mục chưa xong vào bảng "Chuyển sang ngày sau".
6. *(P2, nếu còn thời gian)* Chạy mutation testing `mutmut` trên `stats/` để kiểm tra test có thực sự bắt được lỗi.

---

## 13. Quy trình thực hiện trong ngày

Nguyên tắc: **qua được gate của bước trước mới sang bước sau**. Stats được viết và test bằng builder **trước** khi có generator, nên hai phần độc lập nhau và lỗi của bên này không che lỗi của bên kia.

| # | Bước | Thời lượng | Đầu ra | Gate |
|---|---|---|---|---|
| 1 | Khởi tạo repo | 0:45 | `uv init`, `pyproject.toml`, `.gitattributes`, khung CI, cấu trúc thư mục | `uv run pytest` chạy được (0 test) |
| 2 | Domain primitives | 1:00 | `pitch`, `thresholds`, `matchtime`, `ids`, `xmodels`, `errors` | Unit test + Hypothesis M8, M9 xanh |
| 3 | Event schema + meta + roster | 1:30 | `events.py`, `meta.py`, `rosters.json`, lệnh `schema` | Test `events`/`meta` + test contract xanh |
| 4 | EventLog + máy trạng thái bóng + MatchState + test builder | 1:30 | `log.py`, `ballstate.py`, `match_state.py`, `tests/builders.py` | Mỗi INV có test âm; test các chuyển trạng thái bóng |
| 5 | Stats P0 + kiểm soát bóng theo thời gian + registry | 2:00 | `result`, `select`, `registry`, `team`, `passing`, `possession` | Unit test mọi metric + M1–M5, M11, M12 trên dữ liệu builder |
| 6 | Generator (`balanced`) + validator | 2:45 | `rng`, `generator` (có `ball_dead` / `ball_recovery`), `validate`, `io` | M10, M12 (20 seed), D1–D3, độ thực tế |
| 7 | Kịch bản + pattern + expectation | 1:15 | `patterns.py`, catalog: `dominant_loser` (P0), `comeback_var` (P1) | Test kịch bản xanh |
| 8 | Replay + CLI + golden | 1:00 | `replay.py`, `cli.py`, `data/matches/*`, `tests/golden/*` | Test replay, CLI smoke, golden xanh |
| 9 | Chỉ số P1 (nếu còn thời gian) | — | `physical.py`, `tactical.py` | Test tương ứng xanh |
| 10 | Gate cuối ngày | 0:30 | Coverage, nghiệm thu 12.8, commit, tick checklist | Definition of Done ở mục 0 |

**Tổng thời gian cho P0 khoảng 12 giờ**, thêm khoảng 1 giờ so với v1.0 do kiểm soát bóng tính theo thời gian. Ngày 1 sẽ khá dài; nên chuyển sẵn `comeback_var` sang sáng Ngày 2 nếu tới 18:00 chưa xong bước 6. Nếu trễ, cắt theo thứ tự: bước 9 → `comeback_var` → `player_physical`. Những phần đã cắt chuyển sang buổi sáng Ngày 2 hoặc ngày dự phòng 26/10. **Không bao giờ cắt** bước 4, 5, 6 và các test đi kèm.

> Gợi ý: dùng **GitHub Copilot Agent Mode** khi viết code (đây là một hero technology trong tiêu chí chấm), và ghi lại vài ví dụ sử dụng để đưa vào README.

---

## 14. Bảng chống bug

| Loại bug | Ví dụ | Phòng tránh (thiết kế) | Phát hiện (test) |
|---|---|---|---|
| Không tái lập được | `random` toàn cục, duyệt `set`, `now()` | `rng_stream`, `sorted()`, ruff TID251 | D1–D4 |
| Số liệu hiển thị lệch số liệu thật | UI hoặc agent tự tính lại | Một registry duy nhất (P4) | M1–M4, M7 |
| Dữ liệu vô lý | Cầu thủ đã bị thay ra vẫn chuyền bóng | `MatchState.apply` + INV-xx | Test âm cho từng INV, M10 |
| Off-by-one thời gian | 45:00 thuộc phút nào, event nằm ở biên cửa sổ | Spec `format_minute`, khoảng nửa mở | Unit test ở biên, M2 |
| Lệch hệ toạ độ | Tính final third cho nhầm đội | Attacking frame + `flip` | M5, unit test `pitch` |
| Chia cho 0 | Tính possession khi chưa có pass nào | `status="insufficient_data"` | Ca rỗng cho từng metric |
| Đếm trùng | Đếm cả `goal` lẫn shot outcome; phạt góc vừa là event vừa là pass | Mỗi sự thật chỉ có một nguồn (mục 4.2) | INV-16, unit test đếm |
| VAR huỷ bàn nhưng tỷ số vẫn tính | | `voided_event_ids` tập trung | Test `select`, INV-19, kịch bản `comeback_var` |
| Kiểm soát bóng sai | Thời gian bóng chết (đá phạt, VAR, ăn mừng) bị cộng cho một đội; bóng bị "giữ" bởi hai đội cùng lúc | `ball_dead` tường minh + một máy trạng thái bóng duy nhất (mục 4.4) | INV-22, INV-23, M1, M11, M12 |
| Schema backend và frontend lệch nhau | | Xuất JSON Schema tự động | Contract test |
| Lỗi riêng của Windows | Lỗi dấu tiếng Việt, golden hash lệch | UTF-8, LF, `.gitattributes` | CI chạy Windows, golden test |
| Replay trôi thời gian | `sleep` tương đối bị cộng dồn | Lịch tuyệt đối | FakeClock: ca bên nhận xử lý chậm |
| Sai tỷ số sau khi tua | Nhảy tới phút 60 nhưng tỷ số vẫn 0–0 | Cờ `catching_up` | Test replay `from_minute` |
| Kịch bản viết sai | Gõ sai tên metric | Validate ngay khi import catalog | `test_catalog_loads` |
| Test chập chờn | Hypothesis sinh dữ liệu khác nhau mỗi lần chạy | `derandomize` trong CI | CI ổn định |

---

## 15. Rủi ro và phương án dự phòng

| Rủi ro | Khả năng | Phương án |
|---|---|---|
| Chỉnh generator cho thực tế ngốn quá nhiều thời gian | Cao | Giới hạn 2:30. Khoảng chấp nhận được đặt rộng. Bàn thắng do kịch bản quyết định, nên câu chuyện demo không phụ thuộc vào việc tinh chỉnh |
| Expectation của kịch bản không thoả với một số seed | Trung bình | Thử lại tất định (mục 8.7); chọn seed demo thành công ngay ở attempt 0 |
| INV-15 (liên tục chuỗi chuyền) khó đảm bảo trong generator | Trung bình | Generator luôn chọn người thực hiện hành động kế tiếp là `recipient`; viết test cho phần này trước |
| Thời gian bóng lăn hoặc tỷ lệ kiểm soát bóng không giống thực tế | Trung bình | Test độ thực tế ở mục 12.5 (bóng lăn 50–65 phút, kiểm soát bóng 35–65%); khi lệch thì chỉnh các khoảng thời gian bóng chết ở mục 8.4 |
| Chưa quen uv trên Windows | Thấp | Dùng `pip` + `venv`. Mọi lệnh còn lại giữ nguyên, chỉ bỏ tiền tố `uv run` |
| Vượt quá thời gian của ngày | Trung bình | Cắt theo thứ tự ở mục 13; không cắt test của P0 |

---

## 16. Hợp đồng cho Ngày 2 trở đi (không được phá vỡ)

| Hợp đồng | Bên dùng | Quy tắc thay đổi |
|---|---|---|
| `Event` schema v1.0 (18 loại, gồm `ball_dead` và `ball_recovery`) + `packages/schemas/event.schema.json` | Ingest, WebSocket, frontend (Ngày 4) | Chỉ **thêm** trường tuỳ chọn. Đổi hoặc xoá trường thì phải nâng `schema_version` và cập nhật golden |
| Máy trạng thái bóng (`ballstate.step`) + `PossessionInterval` | Kiểm soát bóng, timeline trên dashboard | Không đổi luật chuyển trạng thái nếu không cập nhật INV-22, golden và test |
| `StatResult` + `REGISTRY` (tên metric, params) | Claim Verifier, Talking-Point, Guardrail (Ngày 2–3), MCP (Ngày 6) | Không đổi tên metric; thêm metric mới thoải mái |
| `MatchState.snapshot()` → `MatchSnapshot` (gồm `as_of_ms`, `ball_in_play`, `holder_team`, `holder_since_ms`, `possession_ms`, `ball_in_play_ms`, `possession_pct`) | WebSocket / dashboard | Chỉ thêm trường. Giữa hai event, frontend có thể nội suy: nếu `ball_in_play` thì cộng `d = now_ms − as_of_ms` vào `possession_ms[holder_team]` và `ball_in_play_ms` rồi tính lại %. Giá trị server gửi xuống luôn là giá trị chuẩn |
| `replay()` → `ReplayItem` | API server (Ngày 2) | Giữ nguyên ngữ nghĩa của `catching_up` |
| `evidence_event_ids` | Drawer "Why / Evidence" trên UI, Guardrail | Luôn sắp theo `seq`, không trùng lặp |

---

## 17. Quyết định đã chốt (20/10/2026)

| # | Quyết định | Kết quả |
|---|---|---|
| 1 | Quản lý package | ✅ **uv** (dự phòng: pip + venv) |
| 2 | Tên repo / package | ✅ Repo `pundit-copilot`, package Python `pundit` |
| 3 | Cầu thủ Việt Nam hư cấu trong roster | ✅ Mỗi đội 1 cầu thủ, phục vụ chế độ *Player focus* cho fan Việt |
| 4 | Cách tính kiểm soát bóng | ✅ **Theo thời gian bóng lăn**: thời gian đội giữ bóng / tổng thời gian bóng lăn, cập nhật live sau mỗi event (mục 4.4, 7.3). Giữ `pass_share` (kiểu Opta) làm chỉ số đối chiếu, mức P1 |

---

## 18. Ghi chú triển khai (as-built, v1.2)

### 18.1. Kết quả

| Hạng mục | Kết quả |
|---|---|
| Code | `backend/src/pundit/`: 40 file Python; ruff và mypy `strict` không có lỗi |
| Test | 483 test (479 chạy mặc định + 4 `slow`); khoảng 40 giây khi không đo coverage; coverage 95.7% (ngưỡng CI 85%) |
| Quét rộng | 100 seed × 4 kịch bản = 400 trận: mọi invariant và expectation đều đạt |
| Tốc độ | Sinh 1 trận (gồm kiểm tra toàn bộ) khoảng 0.2–0.5 giây; toàn bộ chỉ số P0 cho cả trận khoảng 11 ms |
| Dữ liệu demo | `data/matches/`: `dominant_loser-42`, `comeback_var-7`, `red_card_swing-7` |
| Golden | `dominant_loser-42`, `comeback_var-7` (hash + summary) |
| Môi trường | Python 3.12.15, cài qua uv 0.13.1 |

### 18.2. Khác biệt so với v1.1

1. Event schema có **18** loại. v1.1 ghi nhầm là 19: bản đầu ghi 17 nhưng bảng thực chất chỉ có 16 loại, cộng thêm `ball_dead` và `ball_recovery` thành 18.
2. `offside` có thêm trường `via_var`. Việt vị do VAR phát hiện là dữ kiện tường minh, nên máy trạng thái bóng chỉ cần nhìn trường này thay vì tra loại event qua `related_ids`.
3. `ScriptedCard` chỉ hỗ trợ thẻ đỏ trực tiếp. Thẻ vàng thứ hai vẫn có thể xảy ra ngẫu nhiên trong trận.
4. `momentum` là tỷ phần điểm đe doạ trong cửa sổ, không dùng EMA. Cách này ổn định hơn khi dùng để kiểm chứng claim; biểu đồ dùng cửa sổ trượt.
5. `chaos_index` là chỉ số cấp trận, tính theo phút bóng lăn, với các mốc chuẩn hoá được hiệu chỉnh từ dữ liệu sinh ra. `StatResult` có thêm trường `label`.
6. Thêm metric `var_overturns`. `box_entries` (P1) đã được triển khai.
7. INV-17 được mở rộng để kiểm tra mọi loại restart, không chỉ kick-off.
8. `MatchState` có thêm `ball_carrier` (cầu thủ đang có bóng, dùng cho UI và test builder). Snapshot có thêm `phase`, `booked`, `sent_off`.
9. CLI có thêm lệnh `scenarios` và các tuỳ chọn `--compact`, `--max-events`, `--no-physical`.
10. Khi hết giờ mà bóng đang lăn, tiếng còi đến sau 0.5–2 giây. Nếu không, có thể sinh ra possession dài 0 ms (vi phạm INV-23).
11. Khi bóng ra ngoài, điểm bóng ra sân luôn cách người chuyền ít nhất 3 m. Nếu không, có thể sinh ra đường chuyền với tốc độ vô lý (vi phạm INV-07).
12. Ảnh chụp thể lực (`player_physical`) được ghi mỗi 5 phút và một lần cuối mỗi hiệp. Dải độ thực tế về số event không tính loại event này.

### 18.3. Tham số hiệu chỉnh của generator (`engine/generator.py`)

| Tham số | v1.1 | Thực tế | Lý do |
|---|---|---|---|
| Xác suất sút trong vòng cấm | 0.10 + 0.25·threat | 0.08 + 0.16·threat | Đội áp đảo sút quá nhiều (khoảng 40 cú một hiệp) |
| Xác suất sút ngoài vòng cấm | 0.02 + 0.06·threat | 0.015 + 0.04·threat | Như trên |
| Hướng chuyền `dx` | N(−2 + 14·threat, 9) | N(−2 + 10·threat, 9) | Possession quá ngắn |
| Việt vị | 0.05 + 0.05·threat | 0.02 + 0.03·threat | Giảm từ 5 xuống 2 lần mỗi đội |
| Phạm lỗi | 0.012 | 0.016 | Đạt khoảng 8–9 lỗi mỗi đội |
| Thời gian giữ bóng trước hành động | 0.6–3.0 s | 1.0–3.8 s | Giảm từ 640 xuống khoảng 530 đường chuyền mỗi đội |
| Chờ throw-in · goal kick · free kick | 8–15 · 15–30 · 20–45 s | 10–18 · 18–32 · 22–45 s | Thời gian bóng lăn sát thực tế hơn |
| Điểm cuối đường chuyền open play | x ≤ 104 | x ≤ 97; tạt bóng tới x 90–99 | xG mỗi cú sút quá cao (khoảng 4 bàn mỗi trận) |
| Bóng hỏng ra ngoài | — | 50% quả tạt hỏng ra ngoài; 45% bóng qua đường biên ngang được tính là chạm hậu vệ, thành phạt góc | Tăng từ 1 lên khoảng 6 quả phạt góc mỗi đội |
| `dominant_loser` hiệp 2 (HAR) | control 0.78, press 0.8, threat 0.85 | control 0.82, press 0.8, threat 0.45 | Áp đảo bằng kiểm soát bóng thay vì sút liên tục |

**Kết quả trận `balanced`** (16 seed, trung vị mỗi đội): 536 đường chuyền, 85% chính xác, 11 cú sút, 4 trúng đích, xG 1.7, 8 lỗi, 6 phạt góc, 2 việt vị, kiểm soát bóng 50%; thời gian bóng lăn khoảng 61 phút mỗi trận.

**Kết quả `dominant_loser` hiệp 2:** HAR kiểm soát bóng khoảng 61–64% theo thời gian và khoảng 60–62% theo đường chuyền.

**Giới hạn đã biết:** thời gian bóng lăn khoảng 61 phút hơi cao so với Ngoại hạng Anh thực tế (55–58 phút), nhưng vẫn nằm trong dải chấp nhận 50–65 phút.
