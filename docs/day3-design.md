# Detailed Design — Ngày 3 (23/10): Studio crew · Talking-Point · Graphics · Vietnamese Commentator · Người mới xem · Recap · Personalization

> Liên quan: [claude.md](../claude.md) · [checklist.md](../checklist.md) (mục Ngày 3) · [day1-design.md](day1-design.md) · [day2-design.md](day2-design.md) (v1.2; §17 là các hợp đồng Ngày 2 dành cho Ngày 3)
> Phiên bản: v1.0 (22/10/2026) · Người thực hiện: 1 người · Python 3.12
> Chiến lược: **template trước, LLM trong ngân sách.** Chưa gắn Foundry: mock là mặc định cho dev và CI, Ollama dùng để chạy offline với LLM thật, Foundry gắn sau chỉ bằng cấu hình (giống Ngày 2).

---

## 0. Mục tiêu, phạm vi và Definition of Done

Ngày 2 đã có **bộ não kiểm chứng** (claim) và **dòng moment**. Ngày 3 xây **studio crew**: một nhóm agent phản ứng với từng khoảnh khắc của trận để sinh nội dung lên sóng. Mọi con số trong nội dung đều lấy từ Stats Tools và đi qua Guardrail.

| Đầu ra | Dành cho | Ưu tiên |
|---|---|---|
| **Talking points** 2–3 câu, **tiếng Anh + tiếng Việt**, có số liệu đã kiểm chứng | Bình luận viên, biên tập viên (Studio Dashboard, Ngày 4) | P0 |
| **Lower-third JSON** có mốc thời gian trận (`match_clock`, `show_at_match_ms`, `duration_ms`) | Trang overlay (Ngày 4), OBS | P0 (đủ 3 template: P1) |
| **Bình luận real-time tiếng Việt** (kèm bản tiếng Anh), hai giọng *analyst* / *casual* | Fan VN Player (Ngày 4), TTS (Ngày 5) | P0 |
| Chế độ **"Người mới xem"**: giải thích luật đúng lúc tình huống xảy ra | Fan mới xem bóng đá | P0 |
| **Recap** cuối hiệp và cuối trận (VI P0, EN P1) | Fan, studio, TTS (Ngày 5) | P0 / P1 |
| **Cá nhân hoá theo từng kết nối**: Analyst / Casual, ngôn ngữ, CLB yêu thích, Player focus | Mọi màn hình | P0 (Player focus P1, "một chỉ số" P2) |

**Definition of Done có ba tầng**, vì Foundry chưa có:

| Tầng | Điều kiện | Kiểm tra bằng |
|---|---|---|
| **A — mock / template** (bắt buộc trong Ngày 3) | 1. Replay `dominant_loser-42` ở 20x: mỗi moment có importance ≥ 2 có đúng 1 talking point (2–3 câu EN + 2–3 câu VI); bàn thắng, VAR huỷ bàn, thẻ đỏ, momentum, hết hiệp và mốc 30' / 75' có lower-third; bình luận chạy liên tục (khoảng 65–105 mục mỗi trận, **khoảng lặng dài nhất ≤ 5 phút trận**); có recap H1 và FULL (VI + EN). 2. **0** số bịa, **0** nêu sai cầu thủ, **0** sai kết quả tình huống trong mọi văn bản đã công bố (oracle trên 4 kịch bản × 3 seed). 3. Analyst và Casual khác nhau rõ (tiêu chí D1–D4, §11.5). 4. "Người mới xem" kích hoạt đúng tập luật của từng kịch bản (§9.4). 5. Toàn bộ test Ngày 1–2 vẫn xanh; eval claim vẫn 20/20, 0 verdict sai. 6. Event → talking point (template) p95 < 300 ms; bật studio không làm replay 1000x chậm quá 20% | `uv run pundit studio --scenario dominant_loser --seed 42 --report`; `uv run pytest`; `uv run pytest -m perf` |
| **B — LLM thật, chạy offline (Ollama)** | `pundit studio --provider ollama --llm-min-importance 2 --report` chạy hết trận: 0 câu bị chặn lọt ra, 0 số bịa, LLM khả dụng (không timeout hay lỗi quá 20%). Đọc lại toàn bộ câu LLM đã đăng, như cách rà soát ở Day 2 §19.10 | `pundit studio ... --provider ollama`, `uv run pytest -m live` |
| **C — Foundry** (khi có Azure, Ngày 5) | LLM chạy **real-time** trong ngân sách (talking point < 3 s, đúng mục tiêu của Rules) | Ngày 5 |

**Ngoài phạm vi Ngày 3:** UI (Ngày 4), audio TTS (Ngày 5), gắn Foundry và deploy (Ngày 5), MCP server (Ngày 6), Speech-to-Text, tạm dừng/tua phiên phát lại, cho LLM tự gọi tool.

---

## 1. Nguyên tắc thiết kế (bổ sung L1–L8 của Ngày 2)

| # | Nguyên tắc | Áp dụng |
|---|---|---|
| S1 | **Template trước, LLM trong ngân sách** | Mục real-time chờ LLM tối đa một ngân sách (talking point 3 s, bình luận 2,5 s). Không kịp thì đăng bản template, **không bao giờ đăng muộn**. Recap (không cần real-time) và chế độ chạy offline được ngân sách dài |
| S2 | **Một nguồn số liệu: FactPack** | Mỗi trigger có một FactPack tính bằng Stats Tools trên khung nhìn đã ghim. Template, LLM và Personalization chỉ **dùng lại** Fact trong pack, không tự tính |
| S3 | **Ghim thời điểm** | Mỗi job dùng `view_at(trigger.seq)`. Replay chạy tiếp cũng không làm số xê dịch (như L3) |
| S4 | **Mọi câu chữ sinh ra đều qua Guardrail** | Kể cả template (như L4). Guardrail studio thêm các luật: đúng cầu thủ, đúng kết quả tình huống, đúng ngôn ngữ, đúng giọng, câu đánh giá phải dẫn số (§12). Văn bản tĩnh (thư viện luật, câu mở đầu theo CLB) được kiểm tra một lần bằng test |
| S5 | **Replay không bao giờ chờ studio** | Mỗi phiên có một hàng đợi job có giới hạn và một worker riêng. Khi quá tải chỉ bỏ trigger importance 1, **không bao giờ bỏ importance ≥ 2** |
| S6 | **Cá nhân hoá là chính sách trên nội dung đã kiểm chứng** | Personalization chọn biến thể, thêm câu mở đầu tĩnh và lời giải thích luật, **không thêm số mới**. Riêng Player focus lấy Fact qua Stats Tools như mọi agent khác |
| S7 | **Tất định** | Cùng chuỗi event và cùng cấu hình mock thì ra cùng item, cùng ID, cùng câu chữ. Chọn câu trong ngân hàng câu bằng `zlib.crc32(trigger.id)`; không dùng `random`, cũng không dùng `hash()` (bị salt khác nhau giữa các tiến trình) |
| S8 | **Nhân quả trên luồng** | Item của studio luôn được phát sau event và moment gây ra nó (`n` lớn hơn) |
| S9 | **Ports & adapters, CI offline** | Như L6 và L8: logic ở `pundit.agents`, MAF ở `pundit.orchestration`, test mặc định không cần mạng và secret |

---

## 2. Kiến trúc

### 2.1. Luồng tổng thể

```
                  ┌────────────────────────────── MatchSession (1 trận) ────────────────────────────────┐
 replay(events) ──► MatchState.apply ─► MomentDetector ─► moment ─┐                                      │
                  │                  └► CueDetector ────► cue ────┤  Trigger (tạo SAU khi đã phát moment) │
                  │                                               ▼                                      │
                  │                        StudioDirector: hàng đợi có giới hạn, 1 worker cho mỗi phiên  │
                  │                                               ▼                                      │
                  │     StudioService.run(trigger, view_at(trigger.seq))   (MAF workflow, mỗi job 1 lần)  │
                  │        StudioIntake ─► FactPackBuilder ─┬─► TalkingPointAgent ─► Guard ─┐            │
                  │                           (Stats Tools) ├─► GraphicsAgent ──────► Guard ─┼─► StudioPublisher
                  │                                         └─► CommentatorAgent ───► Guard ─┘            │
                  │                                               ▼                                      │
                  │     stores: talking_points · lower_thirds · commentary · recaps · traces             │
                  │                                               ▼                                      │
                  │     Hub.publish(kind, canonical) ─► PersonalizationAgent (1 lần / hồ sơ) ─► WS client │
                  │                                                                                      │
                  │  period_end / full_time ─► RecapService (worker riêng, ngân sách dài: template rồi LLM)│
                  └──────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2. Danh sách agent của Ngày 3

| Agent | Loại | Vào → Ra | LLM? |
|---|---|---|---|
| **CueDetector** | Rule, chạy theo luồng event | `Event` → `Cue[]` | Không |
| **StudioIntake** | Tất định | `Trigger` → `StudioJob` (ghim `seq`, chọn nhánh) | Không |
| **FactPackBuilder** | Gọi tool (catalog chung với Ngày 2) | `StudioJob` → `FactPack` | Không |
| **TalkingPointAgent** | LLM hoặc template | `FactPack` → `TalkingPoint` (EN + VI) | Có / Không |
| **GraphicsAgent** | Rule (LLM chọn chỉ số: P1) | `FactPack` → `LowerThird` | Nhẹ |
| **CommentatorAgent** | LLM hoặc template; văn phong VI + bảng thuật ngữ | `FactPack` → `CommentaryItem` | Có / Không |
| **RuleExplainer** | Thư viện tĩnh | `Trigger` → luật cần giải thích | Không |
| **RecapAgent** | LLM hoặc template | `RecapPack` → `Recap` | Có / Không |
| **PersonalizationAgent** | Chính sách tất định (+ Stats Tools cho Player focus) | item + `Profile` → payload theo hồ sơ | Không (P2: viết lại giọng casual bằng LLM) |
| **Guardrail (studio)** | Tất định | văn bản + `FactPack` → `GuardrailReport` | Không |

Thiết kế này đáp ứng các tiêu chí của **Best Multi-Agent System**:
- **Vai trò riêng biệt:** mỗi agent ở bảng trên làm một việc. Ba agent nội dung chạy **song song** trên cùng một FactPack.
- **Handoff có kiểu:** `Trigger → StudioJob → FactPack → Draft → GuardedItem → StudioOutput` (mục 3).
- **Shared state:** `MatchSession` (khung nhìn ghim, moment, các store), FactPack dùng chung cho ba nhánh; Personalization đọc từ store.
- **Phục hồi khi lỗi:** mỗi nhánh có template dự phòng, circuit breaker riêng của studio, ngân sách thời gian; một nhánh hỏng không chặn nhánh khác.
- **Trace:** mỗi job có một trace (§14.6), xem được trên drawer "Agent Trace" của Ngày 4.
- **Một agent đơn lẻ khó làm được:** trong dưới 300 ms (đường template) sinh ra ba sản phẩm khác loại cho cùng một khoảnh khắc, mỗi sản phẩm có Guardrail riêng, rồi cá nhân hoá cho N hồ sơ người xem.

### 2.3. File mới

```
backend/src/pundit/
├─ agents/                       # logic agent, KHÔNG phụ thuộc MAF/FastAPI
│  ├─ cues.py                    # CueDetector (mục 4)
│  ├─ factpack.py                # kế hoạch truy vấn + dựng FactPack (mục 5)
│  ├─ phrasebank.py              # chọn câu tất định bằng crc32, ngân hàng câu theo kind
│  ├─ talking_points.py          # template talking point EN/VI (mục 6)
│  ├─ graphics.py                # template lower-third + bảng chọn theo trigger (mục 7)
│  ├─ commentary.py              # template bình luận VI/EN × analyst/casual (mục 8)
│  ├─ glossary.py                # thuật ngữ + quy tắc văn phong tiếng Việt (dùng cho template và prompt)
│  ├─ rules_library.py           # "Người mới xem": giải thích luật VI/EN + map trigger → luật (mục 9)
│  ├─ recap.py                   # RecapPack + template recap (mục 10)
│  ├─ personalize.py             # Profile, render(item, profile, seen) (mục 11)
│  └─ studio_guard.py            # check_studio: luật Guardrail của studio (mục 12)
├─ llm/                          # thêm port, mock, prompt, DTO, adapter cho studio (mục 13)
├─ orchestration/
│  ├─ studio_steps.py            # các bước thuần async (dùng cho cả MAF lẫn runner asyncio)
│  ├─ studio_workflow.py         # MAF workflow: fan-out / fan-in (mục 14.2)
│  ├─ studio_service.py          # StudioService.run(trigger, view) -> StudioOutput
│  └─ recap_service.py           # recap: template ngay, bản LLM nếu kịp
└─ api/
   ├─ studio.py                  # StudioDirector + store của phiên (mục 14.4)
   ├─ sessions.py · hub.py · ws.py · routes.py   # mở rộng (mục 15)
```

**Quy tắc import một chiều** của Ngày 2 giữ nguyên: `domain ← stats ← state ← engine ← agents ← llm ← orchestration ← api ← cli`. Test quét import được bổ sung các module mới.

### 2.4. Thay đổi ở code Ngày 1–2 (chỉ thêm)

| Chỗ | Thay đổi | Rủi ro | Test giữ an toàn |
|---|---|---|---|
| `agents/contracts.py` | Thêm `WsType`: `talking_point`, `lower_third`, `commentary`, `recap`, `talking_point_status`; thêm `ViolationCode`: `WRONG_ENTITY`, `WRONG_OUTCOME`, `WRONG_LANGUAGE`, `WRONG_REGISTER`; thêm `Degraded`: `writer_fallback`, `llm_skipped`; model mới ở mục 3 | Schema đổi (chỉ thêm) | Contract test, xuất lại schema |
| `agents/guardrail.check` | Chỉ kiểm tra **từ verdict** ("đúng", "sai"...) khi `context.verdict is not None` | Claim luôn có verdict nên hành vi Ngày 2 không đổi | Test Ngày 2 giữ nguyên; test mới "sai lầm", "đúng lúc" không bị chặn trong bình luận |
| `llm/faults.py` | Thêm role `talker`, `commentator`, `designer`, `recapper`; thêm kind `wrong_player`, `wrong_outcome`, `wrong_language` | Cú pháp cũ không đổi | Test `FaultPlan` |
| `llm/settings.py` | Biến `STUDIO_*`, `RECAP_BUDGET_S` (mục 13.6) | Mặc định không đổi hành vi claim | Test settings |
| `api/hub.py` | `Client.profile`; `publish` render theo nhóm hồ sơ; `state(profile)` | `n` phải liên tục với mọi client | Test 2 client khác hồ sơ |
| `api/sessions.py` | Gắn `CueDetector` + `StudioDirector`; huỷ worker khi dừng; `hello` thêm khoá | Test cũ khẳng định đúng tập khoá của `hello` → **cập nhật có chủ đích** | Test shutdown, test hello |
| `api/ws.py` · `routes.py` | Đọc hồ sơ từ query; REST mới; `/healthz` thêm khối `studio` | — | Test API/WS |
| `orchestration/service.py` | Sửa docstring cũ "GitHub Models or Foundry" thành "Ollama, OpenAI-compatible or Foundry" | Không | — |
| `cli.py` | Thêm lệnh `studio`, `recap` | — | Test CLI |

`STUDIO_ENABLED=false` tắt toàn bộ phần studio, trả lại đúng hành vi Ngày 2. Cờ này dùng để so sánh hiệu năng và làm lối thoát khi demo gặp sự cố.

### 2.5. Dependency mới
Không có. MAF đã ghim từ Ngày 2; bước 0 chỉ spike thêm fan-out / fan-in (mục 14.2).

---

## 3. Hợp đồng dữ liệu (`agents/contracts.py`, chỉ thêm)

Tất cả là Pydantic `frozen=True, extra="forbid"` như Ngày 2. Model dành cho frontend được xuất ra `packages/schemas/` (mục 3.9).

### 3.1. Trigger và Cue

```python
CueKind = Literal["shot", "corner", "offside", "var_review", "added_time", "stat_tick", "lull"]
RuleId = Literal[
    "offside", "var", "penalty", "yellow_card", "second_yellow", "red_card", "corner",
    "substitution", "added_time",                       # luật bóng đá
    "xg", "possession", "momentum", "ppda", "chaos", "tempo",   # thuật ngữ số liệu
]
Outcome = Literal["goal", "saved", "blocked", "off_target", "post",
                  "yellow", "second_yellow", "red", "offside", "overturned", "stands"]

class Cue(Contract):
    id: str                      # f"{match_id}-q{n:04d}", đánh số theo thứ tự phát hiện (tất định)
    match_id: str
    kind: CueKind
    team: TeamId | None
    player: PlayerId | None
    period: int
    clock_ms: int
    display_minute: str
    seq: int                     # seq của event kích hoạt
    trigger_event_ids: tuple[str, ...]
    facts: tuple[Fact, ...]      # chỉ Fact, như Moment

class Trigger(Contract):         # đầu vào chung của studio, dựng từ Moment hoặc Cue
    id: str                      # = moment.id hoặc cue.id
    source: Literal["moment", "cue"]
    kind: str                    # MomentKind | CueKind
    importance: Literal[1, 2, 3] # cue luôn là 1; moment giữ mức của Ngày 2
    team: TeamId | None
    player: PlayerId | None
    period: int
    clock_ms: int
    match_ms: int                # để tính show_at của lower-third
    display_minute: str
    seq: int
    trigger_event_ids: tuple[str, ...]
    facts: tuple[Fact, ...]
    rules: tuple[RuleId, ...]    # luật liên quan (mục 9)
    outcome: Outcome | None      # dùng cho luật WRONG_OUTCOME
    involved_players: tuple[PlayerId, ...]  # người sút, kiến tạo, thủ môn, người bị phạm lỗi, cầu thủ vào/ra
```

### 3.2. FactPack

```python
class FactPack(Contract):
    trigger: Trigger
    as_of_seq: int               # = trigger.seq
    clock_display: str           # "63'", "45+2'"
    score: tuple[int, int]       # (đội nhà, đội khách) tại as_of
    facts: tuple[Fact, ...]      # Fact của trigger + Fact bổ sung (mục 5), không trùng key
    headline: tuple[str, ...]    # key của 1–3 Fact quan trọng nhất (template và LLM dùng trước)
    first_rules: tuple[RuleId, ...]  # luật xuất hiện LẦN ĐẦU trong trận ở trigger này
```

### 3.3. TalkingPoint

```python
TalkingAngle = Literal["headline", "stat", "story"]

class TalkingLine(Contract):
    text: str                    # ≤ 140 ký tự (EN) / 160 (VI)
    angle: TalkingAngle
    fact_keys: tuple[str, ...]   # Fact mà Guardrail khớp với các số trong câu

class TalkingPoint(Contract):
    id: str                      # f"{trigger.id}-tp"
    match_id: str
    trigger_id: str
    trigger_kind: str
    importance: Literal[2, 3]
    period: int
    clock_ms: int
    display_minute: str
    as_of_seq: int
    en: tuple[TalkingLine, ...]  # 2–3 câu
    vi: tuple[TalkingLine, ...]  # 2–3 câu
    source_en: Literal["llm", "template"]
    source_vi: Literal["llm", "template"]
    model: str
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    degraded: tuple[Degraded, ...]
    status: Literal["new", "approved", "dismissed", "pinned"] = "new"   # P1, đổi qua REST
    trace: tuple[TraceSpan, ...] | None = None                           # không phát qua WS
```

### 3.4. LowerThird

```python
LowerThirdTemplate = Literal["score_update", "team_compare", "player_card", "momentum"]

class GraphicStat(Contract):
    key: str                     # key của Fact (cặp so sánh: key của bên nhà)
    label_vi: str
    label_en: str
    home: str | None             # display của Fact; dùng cho template so sánh 2 đội
    away: str | None
    value: str | None            # display; dùng cho template 1 bên (player_card)
    highlight: Literal["home", "away"] | None

class TeamBadge(Contract):       # "team_colors" của checklist nằm ở đây
    id: TeamId
    name: str
    short_name: str
    primary_color: str           # "#RRGGBB", lấy từ TeamInfo của Ngày 1
    secondary_color: str

class LowerThird(Contract):
    id: str                      # f"{trigger.id}-lt"
    match_id: str
    trigger_id: str
    template: LowerThirdTemplate
    title_vi: str                # ≤ 32 ký tự
    title_en: str
    subtitle_vi: str             # ≤ 48 ký tự
    subtitle_en: str
    stats: tuple[GraphicStat, ...]   # 1–4 dòng
    home: TeamBadge
    away: TeamBadge
    player: PlayerId | None
    period: int
    clock_ms: int
    match_clock: str             # "63'" — mốc hiển thị
    show_at_match_ms: int        # mốc trên trục thời gian trận, để đồng bộ với video
    duration_ms: int             # thời gian hiển thị ở tốc độ 1x
    priority: Literal[1, 2, 3]
    supersedes: tuple[str, ...] = ()   # VAR huỷ bàn → thay graphic của bàn thắng đó
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    source: Literal["rule", "llm"]
    synthetic: Literal[True] = True
    disclaimer: str              # = DISCLAIMER của Ngày 1
```

### 3.5. Bình luận

```python
Register = Literal["analyst", "casual"]

class CommentaryText(Contract):
    language: Lang
    register: Register
    text: str                    # ≤ 220 ký tự (analyst) / 160 (casual)
    source: Literal["llm", "template"]

class CommentaryItem(Contract):  # bản chuẩn (canonical), chưa cá nhân hoá
    id: str                      # f"{trigger.id}-c"
    match_id: str
    trigger_id: str
    trigger_kind: str
    importance: Literal[1, 2, 3]
    period: int
    clock_ms: int
    display_minute: str
    as_of_seq: int
    team: TeamId | None
    player: PlayerId | None
    texts: tuple[CommentaryText, ...]   # đủ 4 bản: vi/en × analyst/casual
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    rules: tuple[RuleId, ...]
    first_rules: tuple[RuleId, ...]
    excitement: Literal[0, 1, 2, 3]      # cho SSML của Ngày 5
    speak: bool                          # gợi ý đọc thành tiếng (Ngày 5 chọn lọc)
    degraded: tuple[Degraded, ...]
```

### 3.6. Recap

```python
RecapScope = Literal["H1", "FULL"]

class RecapText(Contract):
    language: Lang
    register: Register
    title: str
    paragraphs: tuple[str, ...]  # 3–5 đoạn, tổng 120–220 từ (đọc 1–2 phút)
    source: Literal["llm", "template"]

class Recap(Contract):
    id: str                      # f"{match_id}-recap-{scope}"
    match_id: str
    scope: RecapScope
    as_of_seq: int
    texts: tuple[RecapText, ...] # vi/en × analyst/casual
    key_moment_ids: tuple[str, ...]
    stats: tuple[GraphicStat, ...]
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    revision: int                # 1 = template; 2 = bản LLM (chỉ khi mọi đoạn qua Guardrail)
    degraded: tuple[Degraded, ...]
```

### 3.7. Hồ sơ người xem và payload đã cá nhân hoá

```python
class Profile(Contract):
    mode: Literal["analyst", "casual"] = "analyst"
    language: Lang = "vi"
    team: TeamId | None = None   # CLB yêu thích
    player: PlayerId | None = None   # Player focus (P1)
    beginner: bool = False       # "Người mới xem"
    metric: str | None = None    # "một chỉ số duy nhất" (P2)

    def key(self) -> str: ...    # "casual|vi|harbor_lions|-|1|-": dùng để cache khi render

class FactView(Contract):
    label: str                   # theo ngôn ngữ của hồ sơ
    display: str

class RuleView(Contract):
    rule: RuleId
    title: str
    text: str                    # bản đầy đủ (2–3 câu) hoặc bản ngắn (1 câu)
    depth: Literal["full", "short"]

class CommentaryView(Contract):  # payload của message WS `commentary`
    id: str
    trigger_id: str
    kind: str
    period: int
    clock_ms: int
    display_minute: str
    importance: Literal[1, 2, 3]
    language: Lang
    register: Register
    text: str                    # đúng một CommentaryText đã qua Guardrail
    framing: str | None          # câu mở đầu tĩnh theo CLB (chỉ casual), ví dụ "Đội của bạn ghi bàn!"
    emphasis: Literal["own_team", "opponent", "neutral", "focus_player"]
    relevance: Literal["high", "normal", "low"]
    facts: tuple[FactView, ...]  # analyst: các Fact chính; casual: ()
    explanation: RuleView | None # "Người mới xem"
    player_tracker: tuple[FactView, ...] | None   # Player focus, tại các mốc stat_tick (P1)
    speak: bool
    excitement: int
    source: Literal["llm", "template"]
```

`LowerThirdView` là `LowerThird` đã chọn sẵn theo ngôn ngữ của hồ sơ (`title`, `subtitle`, `stats[].label`). `RecapView` là `Recap` đã chọn sẵn ngôn ngữ và giọng.

### 3.8. Message WebSocket mới

| `type` | Khi nào | `payload` | Cá nhân hoá |
|---|---|---|---|
| `talking_point` | Sau mỗi job có nhánh talking point | `TalkingPoint` (không kèm trace) | Không: studio thấy cả EN và VI |
| `lower_third` | Sau mỗi job có nhánh graphics | `LowerThirdView` | Theo ngôn ngữ |
| `commentary` | Sau **mọi** job | `CommentaryView` | Đầy đủ (mục 11) |
| `recap` | Hết hiệp 1 và hết trận; phát `revision` 1, sau đó 2 nếu có bản LLM | `RecapView` | Ngôn ngữ + giọng |
| `talking_point_status` (P1) | Biên tập viên đổi trạng thái | `{id, status}` | Không |

- `v` vẫn là `1`, vì chỉ **thêm** type. Hợp đồng ghi rõ: **client phải bỏ qua type không biết**.
- `hello` / `resync` thêm các khoá `profile` (echo hồ sơ), `talking_points` (20 gần nhất), `lower_thirds` (5 gần nhất), `commentary` (30 gần nhất, đã cá nhân hoá), `recaps` (bản mới nhất của từng scope).
- Tên `commentary` thay cho `vn_commentary` trong checklist, vì luồng này có cả tiếng Việt lẫn tiếng Anh theo hồ sơ (mục 22).

### 3.9. Mã vi phạm, cờ degraded và schema xuất ra

- `ViolationCode` thêm: `WRONG_ENTITY`, `WRONG_OUTCOME`, `WRONG_LANGUAGE`, `WRONG_REGISTER` (mục 12). Dùng lại `UNGROUNDED`, `FRAGMENT`, `TOO_LONG` của Ngày 2.
- `Degraded` thêm: `writer_fallback` (bản LLM hỏng hoặc bị chặn, dùng template) và `llm_skipped` (không gọi LLM vì breaker mở hoặc không còn ngân sách).
- Schema xuất thêm: `talking_point`, `lower_third_view`, `commentary_view`, `recap_view`, `profile`; `ws_message` được xuất lại.

---

## 4. CueDetector (`agents/cues.py`)

Chạy ngay sau `MomentDetector.feed`, trên cùng khung nhìn. Detector có trạng thái nhưng **tất định**: cùng chuỗi event thì ra cùng danh sách cue, cùng ID. Có `detect_cues(meta, events)` chạy theo lô, và test bảo đảm chạy lô cho kết quả giống chạy theo luồng.

### 4.1. Các loại cue

| Kind | Kích hoạt | Fact | Luật liên quan | Ghi chú |
|---|---|---|---|---|
| `shot` | `ShotEvent` không thành bàn **và** chưa thành moment (`big_chance` / `rocket_shot`) | xG và tốc độ cú sút | `penalty` nếu `situation = penalty` | `outcome` ∈ saved / blocked / off_target / post |
| `corner` | `PassEvent` có `play_type = corner` | Số phạt góc của đội tới lúc đó (bằng chứng: các quả phạt góc) | `corner` | |
| `offside` | `OffsideEvent` | Số lần việt vị của đội | `offside` (+ `var` nếu `via_var`) | |
| `var_review` | `VarCheckEvent`, trừ trường hợp đã thành moment `goal_overturned` | — | `var` (+ `penalty` / `red_card` theo `check_type`) | `outcome` = stands / overturned |
| `added_time` | Event đầu tiên có phút chính xác ≥ 45 (hiệp 1) hoặc ≥ 90 (hiệp 2) | Tỷ số | `added_time` | 2 cue mỗi trận |
| `stat_tick` | Event đầu tiên sau các mốc 15', 30', 60', 75' | Kiểm soát bóng, cú sút, xG của hai đội từ đầu trận; chaos index và nhịp độ 15' gần nhất (cho analyst) | `possession`, `xg` (lần đầu), `chaos`, `tempo` | 4 cue mỗi trận; 45' và 90' đã có moment hết hiệp |
| `lull` | 5 phút trận không có trigger nào | Kiểm soát bóng và số đường chuyền 5' gần nhất của hai đội | `possession` | Bảo đảm bình luận chạy liên tục (khoảng lặng ≤ 5 phút) |

### 4.2. Moment của Ngày 2 → luật liên quan

| Moment | Luật | `outcome` |
|---|---|---|
| `goal` | `penalty` nếu cú sút là phạt đền; `xg` (lần đầu) | goal |
| `goal_overturned` | `var` (+ `offside` nếu `reason = offside`) | overturned |
| `yellow_card` | `yellow_card` | yellow |
| `red_card` | `red_card` hoặc `second_yellow` | red / second_yellow |
| `big_chance` | `xg` (+ `penalty`) | theo cú sút |
| `rocket_shot` | — | theo cú sút |
| `momentum_shift` | `momentum` | — |
| `dangerous_spell` | — | — |
| `substitution` | `substitution` | — |
| `period_start`, `period_end`, `full_time` | — | — |

### 4.3. Số lượng ước tính (đo trên dữ liệu sinh, 22/10)

| Trận | Moment | Cue `shot` | Phạt góc | Việt vị | Bình luận (ước tính, chưa tính `lull`) | Talking point | Lower-third |
|---|---|---|---|---|---|---|---|
| `dominant_loser-42` | 29 | 20 | 14 | 6 | ~75 | 17 | ~10 |
| `comeback_var-7` | 37 | 19 | 11 | 4 | ~77 | 18 | ~15 |
| `red_card_swing-7` | 45 | 27 | 17 | 3 | ~98 | 27 | ~10 |
| `balanced-3` | 30 | 13 | 9 | 6 | ~64 | 15 | ~13 |

Khi chưa có cue `lull`, khoảng lặng dài nhất đo được là 5,7–7,3 phút, trung bình 1,0–1,6 phút. Cue `lull` kéo khoảng lặng dài nhất xuống ≤ 5 phút.

**Lưu ý dữ liệu:**
- Bốn trận mẫu **không có quả phạt đền nào**, và không có thẻ vàng thứ hai.
- VAR chỉ xuất hiện ở `comeback_var-7` (bàn bị huỷ vì việt vị).

Các luật `penalty` và `second_yellow` vì thế được test trên dữ liệu dựng tay (`MatchBuilder`); video demo không phụ thuộc vào chúng.

---

## 5. FactPackBuilder (`agents/factpack.py`)

Tách làm hai phần giống `collector.py` của Ngày 2:
- `plan(trigger) -> list[StatQuery]` nằm trong `agents`.
- Phần thực thi chạy qua catalog tool ở `orchestration/tools.py`, nên mỗi truy vấn là một span `tool` trong trace.

Toàn bộ chạy trên `view_at(trigger.seq)`.

| Trigger | Fact bổ sung (tool `get_stat`) | Headline |
|---|---|---|
| `goal` | tỷ số (2 đội), xG và cú sút cả trận (2 đội), số bàn của cầu thủ ghi bàn | tỷ số, xG cú sút |
| `goal_overturned` | tỷ số (2 đội) | tỷ số |
| `red_card` | thẻ đỏ của đội, kiểm soát bóng 10' gần nhất (2 đội) | thẻ đỏ |
| `yellow_card` | thẻ vàng của đội, số lỗi của cầu thủ cả trận | thẻ vàng |
| `big_chance` | xG và cú sút cả trận (2 đội) | xG cú sút |
| `rocket_shot` | cú sút cả trận của đội | tốc độ cú sút |
| `dangerous_spell` | số lần vào vòng cấm 10' gần nhất (2 đội) | Fact của moment |
| `momentum_shift` | kiểm soát bóng và cú sút 10' gần nhất (2 đội) | momentum |
| `substitution`, `period_start`, `var_review`, `added_time` | tỷ số | — |
| `period_end` / `full_time` | kiểm soát bóng, cú sút, cú sút trúng đích, xG, phạt góc của hiệp / cả trận (2 đội) | tỷ số, kiểm soát bóng, cú sút, xG |
| `shot` | cú sút cả trận của đội | xG cú sút |
| `corner` / `offside` | (Fact của cue) | Fact của cue |
| `stat_tick` | kiểm soát bóng, cú sút, xG (2 đội, từ đầu trận); chaos index và nhịp độ 15' gần nhất | kiểm soát bóng, cú sút, xG |
| `lull` | (Fact của cue) | kiểm soát bóng 5' |

- **Định dạng key** giống Fact của moment: `f"{metric}:{owner}:{window_label}@{as_of_seq}"`. Nhờ vậy `window_of()` của Guardrail nhận ra hai Fact cùng cửa sổ khi so sánh.
- Fact trùng key được gộp; thứ tự Fact tất định (theo bảng trên).
- `first_rules` được tính trên chính chuỗi trigger (luật chưa từng xuất hiện trước đó trong trận), nên tất định và không phụ thuộc client.

---

## 6. Talking-Point Agent (`agents/talking_points.py`)

- **Kích hoạt:** moment có importance ≥ 2 (`goal`, `goal_overturned`, `red_card`, `big_chance`, `dangerous_spell`, `momentum_shift`, `period_end`, `full_time`). Khoảng 15–27 talking point mỗi trận.
- **Ba góc câu:**
  - `headline`: chuyện gì vừa xảy ra, kèm 1 con số.
  - `stat`: một con số so sánh.
  - `story`: câu chuyện của trận, ví dụ áp đảo nhưng đang thua.
- **Luật:**
  - Mỗi câu có ít nhất 1 số từ FactPack (luật `UNGROUNDED` bản chặt).
  - Không câu cụt; đúng cầu thủ, đúng kết quả, đúng ngôn ngữ.
  - Độ dài ≤ 140 ký tự (EN) / 160 (VI).
- **Template:** mỗi `kind × angle × ngôn ngữ` có ít nhất 3 mẫu câu, chọn bằng `crc32`. Ví dụ với bàn gỡ hoà phút 63 của `dominant_loser-42`:
  - EN: "GOAL — Harbor Lions FC level it 1-1 at 63'." · "Calder's finish was a 0.31 xG chance." · "Harbor have out-shot Kestrel 15 to 1 since the break."
  - VI: "BÀN THẮNG — Harbor Lions FC gỡ hoà 1-1 ở phút 63." · "Cú dứt điểm của Calder có xG 0.31." · "Từ đầu hiệp 2, Harbor dứt điểm 15 lần, Kestrel chỉ 1."
  - (Con số trong ví dụ chỉ để minh hoạ; số thật lấy từ FactPack khi chạy.)
- **Đường LLM:**
  - Một lần gọi trả về `{"en": [...], "vi": [...]}` (mỗi ngôn ngữ 2–3 câu); từng câu qua Guardrail.
  - Mỗi ngôn ngữ được nhận nếu có ít nhất 2 câu qua. Không đủ thì retry 1 lần kèm feedback (nếu còn ngân sách), sau đó dùng bộ template của ngôn ngữ đó.
  - Ngân sách: 3 s cho cả talking point.
- **Trạng thái (P1):** `new → approved / dismissed / pinned` qua REST, phát `talking_point_status`. Hàng đợi duyệt / bỏ / ghim ở Ngày 4 chỉ cần gọi API này.

---

## 7. Graphics Agent (`agents/graphics.py`)

### 7.1. Bảng chọn template

| Trigger | Template | Dòng số liệu | `duration_ms` (1x) |
|---|---|---|---|
| `goal` | `score_update` | tỷ số (2 bên), xG cú sút | 10 000 |
| `goal_overturned` | `score_update` (tiêu đề "VAR: KHÔNG CÔNG NHẬN BÀN THẮNG") | tỷ số | 10 000, `supersedes` graphic của bàn bị huỷ |
| `red_card` | `player_card` | thẻ đỏ của đội | 8 000 |
| `momentum_shift` | `momentum` | momentum 10', kiểm soát bóng 10', cú sút 10' | 8 000 |
| `period_end` / `full_time` | `team_compare` | kiểm soát bóng, cú sút, xG, phạt góc | 12 000 |
| `stat_tick` 30' / 75' | `team_compare` | kiểm soát bóng, cú sút, xG | 8 000 |
| `big_chance` / `rocket_shot` (P1) | `player_card` | xG hoặc tốc độ cú sút, số cú sút của cầu thủ | 6 000 |

### 7.2. Luật
- Giá trị số liệu = `Fact.display`. Nhãn lấy từ `wording.metric_label`. Tiêu đề và phụ đề lấy từ ngân hàng câu tĩnh (≤ 32 / 48 ký tự) và **cũng qua Guardrail**. Trên sóng không dùng câu chữ tự do do LLM viết.
- `show_at_match_ms = trigger.match_ms`. Overlay của Ngày 4 tự quy đổi theo tốc độ replay: ở tốc độ v, graphic hiện trong `duration_ms / v` giây thực.
- `priority = importance`. Graphic mới có priority cao hơn được đè lên graphic đang hiện; `supersedes` dùng cho VAR.
- `home` / `away` mang màu áo từ `TeamInfo` (`primary_color`, `secondary_color`).
- Mỗi lower-third mang `synthetic: true` và `disclaimer` (luật bắt buộc "Synthetic data").
- **LLM (P1):** `GraphicsDesigner` chọn template và 2–4 key chỉ số **trong danh sách ứng viên** do code đưa ra. Code kiểm tra lựa chọn (template hợp lệ với kind, key nằm trong danh sách); sai thì dùng bảng 7.1. LLM chỉ "chọn", không viết số.

---

## 8. Vietnamese Commentator Agent (`agents/commentary.py`, `agents/glossary.py`)

### 8.1. Kích hoạt và các bản văn bản
- **Mọi** moment và cue: khoảng 65–105 mục mỗi trận (mục 4.3), phát theo đúng thứ tự trigger.
- Mỗi mục có đủ **4 bản**: `vi` / `en` × `analyst` / `casual`. Template luôn sinh đủ 4 bản.
- Khi được phép, LLM viết lại hai bản `vi/analyst` và `vi/casual` **trong một lần gọi**. Được phép nghĩa là: importance ≥ `STUDIO_LLM_MIN_IMPORTANCE`, breaker đóng, còn ngân sách. Bản EN do LLM viết là P1.

| Giọng | Quy tắc |
|---|---|
| `analyst` | 1–2 câu, ít nhất 1 số, được dùng thuật ngữ (xG, PPDA, momentum, chaos index), nêu so sánh |
| `casual` | 1–2 câu ngắn, **tối đa 1 số**, không thuật ngữ (thay bằng lời thường), cảm xúc theo `excitement` |

Ví dụ (minh hoạ):
- **Cú sút bị cứu thua, analyst:** "Phút 34, Calder dứt điểm (xG 0.12), thủ môn cứu thua. Harbor đã có 6 cú sút."
- **Cú sút bị cứu thua, casual:** "Calder tung cú sút khá đẹp nhưng thủ môn đã cản phá!"
- **Bàn gỡ hoà, casual:** "VÀOOO! Calder gỡ hoà 1-1 cho Harbor!"

### 8.2. Văn phong và bảng thuật ngữ (`glossary.py`)
- **Khoảng 30 thuật ngữ** có cách dùng chuẩn, ví dụ: dứt điểm, cứu thua, cản phá, chệch cột dọc, dội cột, bị chặn, đánh đầu, sút xa, phạt góc, tình huống cố định, việt vị, phản công, lật cánh, pressing tầm cao, dồn ép, kiểm soát bóng, thế trận, bù giờ, thẻ vàng, thẻ đỏ, vào sân thay, rời sân, gỡ hoà, vươn lên dẫn, cơ hội ngon ăn, bàn thắng kỳ vọng (xG).
- **Giọng điệu:**
  - Trung lập theo mặc định; góc nhìn CLB chỉ đến từ câu mở đầu tĩnh (mục 11.3).
  - Không phán xét cầu thủ hay trọng tài (khớp danh sách từ cấm của Guardrail).
- **Tên riêng:** cầu thủ Việt dùng `short_name` ("Gia Bảo"); cầu thủ khác dùng họ ("Calder"); đội dùng tên đầy đủ hoặc mã 3 chữ.
- **Số:** viết đúng `Fact.display` (dấu chấm thập phân, như Ngày 2).
- **Không lặp câu:** ngân hàng câu có ít nhất 3 mẫu cho mỗi `kind × giọng × ngôn ngữ`, chọn bằng `crc32(trigger.id)`.

### 8.3. Mức cảm xúc và gợi ý đọc (cho Ngày 5)

| Trigger | `excitement` |
|---|---|
| Bàn thắng (gỡ hoà / vươn lên / phút 80+ là 3) | 2–3 |
| VAR huỷ bàn | 3 |
| Thẻ đỏ | 2 |
| Cơ hội lớn | 2 |
| Cú sút cực mạnh, momentum đổi chiều | 1 |
| Còn lại | 0 |

`speak = importance ≥ 2` hoặc kind ∈ {`stat_tick`, `added_time`, `period_end`, `full_time`}. Ngày 5 sẽ giới hạn tốc độ đọc thêm một lần nữa.

### 8.4. Đường LLM
- Prompt `commentary-vi-v1`. Phần tĩnh gồm vai trò, quy tắc văn phong, bảng thuật ngữ, ràng buộc số và giọng; phần này đặt ở system message để Ollama dùng lại cache.
- Phần user là FactPack thu gọn: Fact dạng nhãn + display, cùng tóm tắt trigger (kind, phút, đội, tên cầu thủ, kết quả).
- Output `{"analyst": "...", "casual": "..."}`. Mỗi bản qua Guardrail riêng; bản nào trượt thì dùng template cho riêng bản đó.
- Ngân sách 2,5 s; không retry trên đường real-time (retry làm lỡ thời điểm).

---

## 9. "Người mới xem" (`agents/rules_library.py`)

### 9.1. Thư viện (văn bản tĩnh, VI + EN, có tiêu đề, bản ngắn 1 câu và bản đầy đủ 2–3 câu)

| `RuleId` | Nội dung bản đầy đủ (tiếng Việt) |
|---|---|
| `offside` | Cầu thủ tấn công bị việt vị nếu ở phần sân đối phương, đứng gần đường biên ngang cuối sân hơn cả bóng lẫn cầu thủ phòng ngự áp chót, đúng lúc đồng đội chuyền bóng, và có tham gia vào tình huống. Không có việt vị khi nhận bóng trực tiếp từ quả ném biên, phạt góc hay phát bóng. Đội phòng ngự được hưởng một quả đá phạt gián tiếp. |
| `var` | VAR (trợ lý trọng tài video) chỉ can thiệp khi có sai sót rõ ràng ở bốn loại tình huống: bàn thắng, phạt đền, thẻ đỏ trực tiếp và nhầm người. Trọng tài có thể ra màn hình bên sân xem lại rồi giữ nguyên hoặc đổi quyết định. |
| `penalty` | Phạt đền được thổi khi đội phòng ngự phạm lỗi (như đá người, kéo người, chạm tay) ngay trong vòng cấm của mình. Bóng đặt ở chấm phạt đền, chỉ còn người sút đối mặt thủ môn. |
| `yellow_card` | Thẻ vàng là lời cảnh cáo, ví dụ cho lỗi phạm lỗi chiến thuật, phản ứng trọng tài hay câu giờ. Nhận hai thẻ vàng trong cùng một trận thì bị truất quyền thi đấu. |
| `second_yellow` | Thẻ vàng thứ hai trong trận đồng nghĩa với thẻ đỏ: cầu thủ phải rời sân và đội phải chơi thiếu người tới hết trận. |
| `red_card` | Thẻ đỏ trực tiếp dành cho lỗi nghiêm trọng như vào bóng nguy hiểm, hành vi bạo lực hay phạm lỗi để chặn một cơ hội ghi bàn rõ ràng. Cầu thủ phải rời sân ngay, đội chơi thiếu một người và không được thay người bù vào. |
| `corner` | Đội tấn công được hưởng phạt góc khi cầu thủ đội phòng ngự là người chạm bóng cuối cùng trước khi bóng đi hết đường biên ngang (mà không thành bàn). Quả phạt góc đá từ góc sân và có thể ghi bàn trực tiếp. |
| `substitution` | Mỗi đội được thay tối đa năm cầu thủ, trong không quá ba lần dừng trận (không tính giờ nghỉ giữa hiệp). Cầu thủ đã rời sân thì không được vào lại. |
| `added_time` | Bù giờ là thời gian cộng thêm cuối mỗi hiệp để bù cho các lần dừng trận như thay người, chấn thương, VAR hay ăn mừng bàn thắng. Bảng báo bù giờ là thời gian tối thiểu; trọng tài có thể cho đá lâu hơn. |
| `xg` | xG (bàn thắng kỳ vọng) ước lượng khả năng một cú sút thành bàn, từ 0 đến 1, dựa trên vị trí và kiểu dứt điểm. Tổng xG cao cho thấy một đội tạo được nhiều cơ hội tốt, dù chưa chắc đã ghi bàn. |
| `possession` | Kiểm soát bóng ở đây tính theo thời gian bóng lăn: mỗi đội giữ bóng bao nhiêu phần trăm thời gian bóng đang trong cuộc, không tính lúc bóng chết. Cầm bóng nhiều hơn chưa chắc đã nguy hiểm hơn. |
| `momentum` | Momentum (thế trận) so sánh mức đe doạ tấn công của hai đội trong một khoảng thời gian, từ -1 đến 1, tính từ cú sút, số lần vào 1/3 cuối sân và vòng cấm, phạt góc và số lần đoạt bóng ở phần sân đối phương. Số dương nghĩa là thế trận nghiêng về đội đang xét. |
| `ppda` | PPDA đo cường độ pressing: số đường chuyền đối phương được thực hiện cho mỗi pha phòng ngự (tắc bóng, cắt bóng, phạm lỗi) của đội mình ở phần sân phía trên. Càng thấp nghĩa là pressing càng rát. |
| `chaos` | Chỉ số "kiểm soát hay hỗn loạn", từ 0 đến 1: cao khi bóng đổi chân liên tục và không đội nào làm chủ; thấp khi trận đấu có trật tự hoặc một đội áp đảo rõ. |
| `tempo` | Nhịp độ là số đường chuyền mỗi phút một đội giữ bóng: cao nghĩa là đội đó chuyền nhanh, đẩy nhịp trận lên. |

- Văn bản thư viện **tĩnh, được duyệt bằng tay** và không đi qua Guardrail lúc chạy. Có test tự động kiểm tra:
  - Đủ VI và EN.
  - Độ dài (bản ngắn ≤ 140 ký tự, bản đầy đủ ≤ 420).
  - Không chứa từ cấm.
  - Không chứa số của trận đấu.
- Các định nghĩa số liệu bám đúng định nghĩa trong `stats/registry.py`. Có test so khoá để mỗi `RuleId` thuộc nhóm số liệu đều có metric tương ứng trong registry.

### 9.2. Kích hoạt
Luật được gắn vào trigger (bảng 4.1, 4.2); `first_rules` đánh dấu lần xuất hiện đầu tiên trong trận. Luật nhóm số liệu (`xg`, `possession`, `momentum`, `ppda`, `chaos`, `tempo`) chỉ gắn khi văn bản casual hoặc analyst của mục đó thực sự nhắc tới chỉ số ấy.

### 9.3. Cách hiển thị
- Chỉ khi `profile.beginner = true`.
- Qua WebSocket: luật hiện **bản đầy đủ** ở lần đầu client gặp nó kể từ lúc kết nối (`Client.seen_rules`). Các lần sau không hiện nữa, trừ `red_card`, `second_yellow` và `penalty` (hiện bản ngắn mỗi lần).
- Qua REST (không có trạng thái theo client): dùng `first_rules` (lần đầu trong trận).
- Lời giải thích là trường `explanation` riêng, **không trộn vào câu bình luận**, để TTS của Ngày 5 có thể đọc tách riêng.

### 9.4. Luật kỳ vọng theo kịch bản (dùng làm test)

| Trận | Luật phải xuất hiện | Luật không được xuất hiện |
|---|---|---|
| `dominant_loser-42` | offside, corner, yellow_card, substitution, added_time, xg, possession, momentum | var, penalty, red_card, second_yellow |
| `comeback_var-7` | như trên + var (việt vị, bàn bị huỷ ở khoảng 36') | penalty, red_card |
| `red_card_swing-7` | như `dominant_loser` + red_card (52') | var, penalty, second_yellow |
| `balanced-3` | offside, corner, yellow_card, substitution, added_time, xg, possession, momentum | var, penalty, red_card |
| Dữ liệu dựng tay | penalty (cú sút `situation = penalty`), second_yellow, var `stands` | — |

Bảng này sẽ được chốt lại bằng dữ liệu thật ở bước 2 (mục 18); nếu khác, cập nhật kèm lý do ở mục as-built.

---

## 10. Recap Agent (`agents/recap.py`, `orchestration/recap_service.py`)

- **Kích hoạt:** moment `period_end` (scope `H1`) và `full_time` (scope `FULL`). Recap chạy trên worker riêng nên không chặn bình luận real-time.
- **RecapPack** (Stats Tools, ghim tại `seq` của moment):
  - tỷ số;
  - danh sách bàn thắng (phút, người ghi, kiến tạo; bỏ bàn bị VAR huỷ);
  - thẻ; quyết định VAR;
  - bảng số liệu của scope: kiểm soát bóng, cú sút, cú sút trúng đích, xG, phạt góc, phạm lỗi (2 đội);
  - với `FULL`: so sánh hai hiệp (kiểm soát bóng, cú sút, xG của từng hiệp) để kể "câu chuyện" trận;
  - 3–5 moment then chốt (importance 3, rồi 2).
- **Template:** đủ 4 bản (vi/en × analyst/casual). Mỗi bản có tiêu đề và 3–5 đoạn: kết quả → diễn biến bàn thắng → câu chuyện số liệu → thẻ / VAR → khoảnh khắc then chốt. Tổng 120–220 từ, vừa 1–2 phút đọc ở Ngày 5.
- **LLM:**
  - Viết các đoạn cho `vi` (P0) và `en` (P1), mỗi ngôn ngữ một lần gọi. Mỗi đoạn qua Guardrail studio.
  - Chỉ khi **mọi đoạn đều qua** mới phát `revision = 2`; nếu không thì giữ bản template (`revision = 1`, cờ `writer_fallback`).
  - Ngân sách `RECAP_BUDGET_S`: mock 5 s, Ollama 200 s, OpenAI-compatible và Foundry 30 s.
- **Phát hành:** phát `recap` với `revision = 1` ngay khi có moment, rồi `revision = 2` nếu bản LLM qua Guardrail.
- **REST:** `GET /matches/{id}/recap?scope=H1|FULL&lang=vi&mode=casual`:
  - `404` nếu trận không tồn tại;
  - `409` (`recap_not_ready`) nếu chưa tới mốc của scope;
  - `200` kèm `RecapView` nếu đã có.

---

## 11. Personalization Agent (`agents/personalize.py` + `api/hub.py`)

### 11.1. Hồ sơ
- Query của WebSocket: `/ws/match/{id}?mode=casual&lang=vi&club=HAR&player=harbor_lions-09&beginner=1&metric=xg`.
  - `club` nhận id đội hoặc mã 3 chữ, không phân biệt hoa thường.
  - `player` nhận id cầu thủ.
  - `beginner` nhận `1/0/true/false`.
- REST dùng cùng tên tham số.
- **Hồ sơ mặc định** (khi không có query) là `analyst + vi`, không CLB, không Player focus. Client của Ngày 2 không bị ảnh hưởng.
- **Hồ sơ sai** (giá trị lạ, đội hoặc cầu thủ không thuộc trận): WebSocket nhận kết nối rồi đóng với mã `4422` và lý do cụ thể; REST trả `422`.

### 11.2. Chính sách

| Khía cạnh | Analyst | Casual |
|---|---|---|
| Văn bản bình luận | `texts[lang][analyst]` | `texts[lang][casual]` |
| `facts` kèm theo | Có (nhãn + display theo ngôn ngữ) | Không |
| Lời giải thích luật | Khi `beginner = 1` | Khi `beginner = 1` |
| Mục importance 1 của đối thủ (khi có CLB yêu thích) | `relevance = normal` | `relevance = low` |
| Recap | Giọng analyst | Giọng casual |
| Lower-third | Theo ngôn ngữ (giống nhau cho hai giọng: nội dung lên sóng) | như analyst |
| Talking point | Không cá nhân hoá (nội dung cho studio) | — |

### 11.3. CLB yêu thích
- `emphasis` là `own_team` / `opponent` / `neutral` theo đội của trigger.
- **Casual** có thêm `framing`: câu mở đầu lấy từ ngân hàng tĩnh, ví dụ "Đội của bạn ghi bàn!", "Tiếc quá, đối thủ vừa có bàn.", "Đội của bạn phải chơi thiếu người."
  - Ngân hàng câu không chứa số và được test kiểm tra từ cấm.
  - Dùng "đội của bạn" chứ không dùng "đội nhà", vì "đội nhà" đã mang nghĩa đội chủ sân trong bộ từ điển claim của Ngày 2.
- **Analyst** chỉ có `emphasis`, không có `framing`: giọng chuyên gia giữ trung lập.

### 11.4. Player focus (P1) và "một chỉ số" (P2)
- **Player focus:**
  - `emphasis = focus_player` và `relevance = high` khi cầu thủ có trong `involved_players` hoặc là chủ của một Fact.
  - Tại các mục `stat_tick`, payload có thêm `player_tracker`: số đường chuyền, cú sút, xG, quãng chạy, tốc độ tối đa của cầu thủ.
  - Các số này tính bằng Stats Tools trên khung nhìn ghim tại `seq` của tick, cache theo `(tick, player)`, và nhãn đi qua Guardrail.
  - Dùng sẵn event `player_physical` mà Ngày 1 sinh mỗi 5 phút.
- **"Một chỉ số"** (`metric=xg`): tại `stat_tick`, `facts` chỉ giữ chỉ số đó; văn bản casual chọn câu mẫu nói về chỉ số đó nếu có.

### 11.5. Đo "khác biệt rõ ràng" giữa Analyst và Casual
Đo trên mọi mục bình luận của 4 kịch bản:

| # | Tiêu chí | Ngưỡng |
|---|---|---|
| D1 | Mục có ≥ 1 Fact: văn bản analyst ≠ văn bản casual | 100% |
| D2 | Số con số trong analyst ≥ trong casual ở mọi mục; lớn hơn hẳn ở các mục có ≥ 2 Fact | 100% / ≥ 80% |
| D3 | Văn bản casual không chứa thuật ngữ (xG, PPDA, momentum, chaos index, nhịp độ) | 100% |
| D4 | Mọi văn bản đúng ngôn ngữ (`detect_language`) | 100% |

Thêm một kiểm tra cấp payload: với cùng một moment, hai client analyst và casual nhận `text`, `facts` và `explanation` khác nhau. Đây là bằng chứng cho yêu cầu "hai trải nghiệm thật sự khác nhau" của Rules, dùng được cho video demo.

### 11.6. Tích hợp với Hub
- `Hub.subscribe(maxsize, profile)`. Mỗi `Client` có `profile` và `seen_rules`.
- `Hub.publish(kind, canonical)`:
  - Với kind cần cá nhân hoá, **render một lần cho mỗi khoá hồ sơ** rồi gửi cho các client cùng khoá.
  - Mọi client nhận **cùng `n`**. **Không client nào bị bỏ message**: mục "không liên quan" vẫn được gửi với `relevance = low`.
  - Nhờ vậy cơ chế phát hiện mất message bằng `n` của Ngày 2 vẫn đúng.
- `hello` / `resync` render theo hồ sơ của từng client.
- Giới hạn tài nguyên: tối đa 64 client và 32 khoá hồ sơ khác nhau mỗi phiên. Vượt quá thì đóng kết nối mới với mã `4429`.

---

## 12. Guardrail của studio (`agents/studio_guard.py`)

### 12.1. Dùng lại `guardrail.check` của Ngày 2
- `facts = pack.facts`.
- Ngữ cảnh được phép: phút của trigger (kể cả "45+2"), tỷ số tại `as_of`, mốc cửa sổ của các Fact.
- Hướng so sánh và chủ sở hữu của con số (`WRONG_COMPARISON`, `WRONG_ATTRIBUTION`) chạy y nguyên.
- Thay đổi duy nhất: chỉ kiểm tra từ verdict khi `context.verdict is not None`. Bình luận được dùng "sai lầm", "đúng lúc".

### 12.2. `check_studio(text, pack, *, language, register, model_text, product)`

| Mã | Áp dụng cho | Luật |
|---|---|---|
| `UNVERIFIED_NUMBER`, `WRONG_COMPARISON`, `WRONG_ATTRIBUTION`, `UNSAFE_CONTENT`, `EMPTY`, `TOO_LONG`, `FRAGMENT` | Mọi văn bản | Như Ngày 2; giới hạn độ dài theo sản phẩm (mục 3) |
| `WRONG_ENTITY` (mới) | Mọi văn bản | Cầu thủ được nêu tên phải thuộc `involved_players` của trigger, hoặc là chủ của một Fact trong pack |
| `WRONG_OUTCOME` (mới) | Trigger có `outcome` (cú sút, thẻ, việt vị, VAR) | Từ chỉ kết quả phải khớp `outcome`, xem bảng 12.3 |
| `WRONG_LANGUAGE` (mới) | Văn bản model viết | `detect_language(text)` phải bằng ngôn ngữ yêu cầu (bỏ qua tên riêng và thuật ngữ như xG, PPDA) |
| `WRONG_REGISTER` (mới) | Văn bản model viết | analyst phải có ≥ 1 số; casual có tối đa 1 số và không chứa thuật ngữ |
| `UNGROUNDED` | Văn bản model viết | Talking point: mỗi câu phải có số. Bình luận và recap: câu có từ đánh giá ("áp đảo", "lấn lướt", "vượt trội", "tốt hơn", "dominate", "on top", "better"...) hoặc từ so sánh phải có số |

### 12.3. Bảng từ chỉ kết quả (WRONG_OUTCOME)

| `outcome` của trigger | Được dùng | Bị cấm |
|---|---|---|
| `saved` | cứu thua, cản phá, bắt gọn; saves, keeps it out | ghi bàn, vào lưới, lập công, gỡ hoà, vươn lên; scores, goal!, equaliser |
| `blocked` | bị chặn; blocked | như trên + cứu thua; saves |
| `off_target` | chệch, ra ngoài, vọt xà; wide, off target, over the bar | như trên |
| `post` | dội cột, trúng xà; hits the post / bar | như trên |
| `goal` | mọi từ chỉ bàn thắng | cứu thua, chệch; saved, wide |
| `yellow` / `red` / `second_yellow` | đúng màu thẻ | màu thẻ khác |
| `offside` / VAR vì việt vị | việt vị; offside | — |

Cụm "bàn thắng kỳ vọng" và "expected goals" không được coi là từ chỉ bàn thắng.

### 12.4. Văn bản tĩnh
Thư viện luật và ngân hàng câu mở đầu theo CLB được kiểm tra một lần bằng test (từ cấm, độ dài, không có số của trận), không đi qua `check_studio` lúc chạy.

### 12.5. Hiệu năng
- Mục tiêu trung bình ≤ 5 ms cho mỗi lần kiểm tra.
- Kết quả `find_entities` được cache theo `(text, match_id)` trong một job, vì các luật quan hệ và luật thực thể đều cần nó.

---

## 13. Tầng LLM

### 13.1. Port (`llm/ports.py`)

```python
class TalkingPointWriterPort(Protocol):
    model: str
    async def write(self, pack: FactPack, feedback: list[GuardrailViolation] | None = None) -> TalkingDraft: ...
        # TalkingDraft: en: tuple[str, ...], vi: tuple[str, ...]

class CommentatorPort(Protocol):
    model: str
    async def comment(self, pack: FactPack, feedback: list[GuardrailViolation] | None = None) -> CommentaryDraft: ...
        # CommentaryDraft: analyst: str, casual: str  (tiếng Việt; EN là P1)

class GraphicsDesignerPort(Protocol):   # P1
    model: str
    async def choose(self, pack: FactPack, options: GraphicsOptions) -> GraphicsChoice: ...

class RecapWriterPort(Protocol):
    model: str
    async def write(self, pack: RecapPack, language: Lang, register: Register,
                    feedback: list[GuardrailViolation] | None = None) -> RecapDraft: ...
```

### 13.2. Prompt (`llm/prompts.py`, có version)
- Các prompt: `talking-v1`, `commentary-vi-v1`, `recap-v1`, `graphics-v1`.
- **Phần tĩnh** nằm trong system message, để Ollama dùng lại cache như ở Day 2 §19.10: vai trò, quy tắc số ("chỉ dùng số trong `facts`, viết đúng `display`"), văn phong, bảng thuật ngữ, ràng buộc giọng, danh sách từ chỉ kết quả, cấm phán xét.
- **Phần động** là FactPack thu gọn: `[{label, display}]`, tóm tắt trigger, ngôn ngữ và giọng, feedback của Guardrail khi retry.

### 13.3. DTO (`llm/dto.py`)
- DTO phẳng, `strict` JSON schema (đúng bài học `oneOf` của Ngày 2).
- Chuẩn hoá: bỏ khoảng trắng thừa, bỏ dấu ngoặc kép bao ngoài, cắt theo giới hạn độ dài. Không bao giờ sửa số.

### 13.4. Mock (`llm/mock.py`)
- Trả về đúng output của template, như L7 "mock chính là fallback".
- Bơm lỗi theo role mới:

| Role | Kind có thể bơm |
|---|---|
| `talker` | timeout, error, invalid_json, hallucinate (đổi một số), unsafe, wrong_player, wrong_outcome, wrong_language |
| `commentator` | như `talker` |
| `designer` (P1) | timeout, error, invalid_json, chọn key ngoài danh sách |
| `recapper` | timeout, error, invalid_json, hallucinate, wrong_player |

- Cú pháp giữ như cũ, ví dụ `MOCK_LLM_FAULTS=commentator:wrong_outcome:2,talker:hallucinate`. Bộ đếm lỗi được làm mới **cho mỗi job**, nên kịch bản demo lặp lại được.

### 13.5. Adapter (`llm/maf_backend.py`)
`LlmTalkingPointWriter`, `LlmCommentator`, `LlmGraphicsDesigner`, `LlmRecapWriter` đều dùng `MafChatBackend.structured`, cùng provider (Ollama / OpenAI-compatible / Foundry) như Ngày 2.

### 13.6. Biến môi trường mới

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `STUDIO_ENABLED` | `true` | Tắt thì quay về đúng hành vi Ngày 2 |
| `STUDIO_LLM_MIN_IMPORTANCE` | `1` | Trigger dưới mức này chỉ dùng template |
| `STUDIO_TP_BUDGET_S` · `STUDIO_COMMENTARY_BUDGET_S` | `3` · `2.5` | Ngân sách real-time |
| `STUDIO_JOB_BUDGET_S` | `4` | Ngân sách cả job (ba nhánh chạy song song) |
| `RECAP_BUDGET_S` | mock `5`, ollama `200`, còn lại `30` | Ngân sách recap |
| `STUDIO_QUEUE_SIZE` | `512` | Giới hạn hàng đợi job của mỗi phiên |
| `STUDIO_DROP_AT` | `256` | Từ độ dài này bắt đầu bỏ trigger importance 1 |

---

## 14. Orchestration

### 14.1. `StudioService.run(trigger, view) -> StudioOutput`
- Mỗi job chạy một **workflow MAF mới** (đã đo ở Day 2 §9.5: build + run ≈ 2,3 ms).
- Có runner `asyncio` dự phòng (`run_studio_pipeline`) dùng chung `studio_steps.py`. Nếu workflow MAF lỗi thì chạy lại bằng runner này, cờ `orchestrator_fallback`, giống ClaimService.
- `StudioOutput = {commentary: CommentaryItem, talking_point: TalkingPoint | None, lower_third: LowerThird | None, trace}`.

### 14.2. Đồ thị workflow (fan-out / fan-in)

```
StudioIntake ─► FactPackBuilder ─┬─► TalkingPointWriter ─► TPGuard ──retry/template──┐
                                 ├─► GraphicsDesigner ───► LTGuard ──rule fallback───┼─► StudioPublisher ─► [StudioOutput]
                                 └─► Commentator ────────► CGuard ──template─────────┘
```

- **Mỗi nhánh luôn gửi đúng một `BranchResult`**, kể cả khi nhánh đó không áp dụng (ví dụ cue importance 1 không có talking point thì gửi `skipped`). Nhờ vậy bước fan-in luôn hoàn tất và không bao giờ treo.
- `StudioPublisher` gộp kết quả theo **thứ tự cố định** (commentary, talking point, lower-third), không phụ thuộc nhánh nào xong trước. Kết quả vì thế tất định.
- **Spike ở bước 0:** MAF Python có `add_fan_out_edges` / `add_fan_in_edges` (cần xác minh tên và hành vi ở bản 1.22.0). Nếu không dùng được, workflow vẫn giữ các executor nhưng nối tuần tự (talking point → graphics → commentary). Ba bước template đều nhanh, nên tổng thời gian chỉ tăng ở đường LLM. Kết quả spike ghi vào mục 23.

### 14.3. Ngân sách, breaker và điều kiện gọi LLM
- Nhánh chỉ gọi LLM khi đủ 3 điều kiện:
  - `trigger.importance ≥ STUDIO_LLM_MIN_IMPORTANCE`;
  - breaker **của studio** đang cho phép;
  - còn ít nhất 0,5 s ngân sách.

  Ngược lại thì dùng template và gắn cờ `llm_skipped`.
- **Studio có circuit breaker riêng**, tách khỏi breaker của ClaimService: ngân sách 3 s của studio làm Ollama timeout liên tục, và không được để điều đó mở mạch của claim (claim với Ollama có timeout 90 s). Breaker studio: 3 lỗi liên tiếp thì mở 60 s, như Ngày 2.
- Với Ollama trên CPU, vài job đầu sẽ timeout, sau đó breaker mở và replay chạy hoàn toàn bằng template. Đây là hành vi mong muốn của S1. Với Foundry (≈ 1 s), LLM chạy thật real-time.
- **Chế độ offline** (`pundit studio`): không có hàng đợi, không bỏ job; ngân sách lấy theo provider (Ollama 90 s mỗi lần gọi); mặc định chỉ gọi LLM cho trigger importance ≥ 2 để một trận chạy trong khoảng 20 phút.

### 14.4. StudioDirector (`api/studio.py`)
- Mỗi phiên có một director. Sau khi phát `moment` / `cue`, replay gọi `director.offer(trigger)`. Hàm này **không chờ**: chỉ `put_nowait` vào hàng đợi.
- Một worker duy nhất xử lý tuần tự, nên item được phát đúng thứ tự trigger.
- **Bỏ job khi quá tải:** khi hàng đợi dài ≥ `STUDIO_DROP_AT`, trigger importance 1 mới bị bỏ và đếm vào `dropped`; trigger importance ≥ 2 luôn được nhận. Mỗi trận chỉ có 15–30 trigger importance ≥ 2 (mục 4.3), nên hàng đợi 512 không thể đầy.
- **Bắt kịp** (`from_minute > 0`): trigger của các event đang bắt kịp (không phát) không sinh job, cùng nguyên tắc với moment. Detector vẫn được nạp đủ event để giữ trạng thái đúng.
- **Dừng phiên:** huỷ worker và bỏ các job còn chờ (đếm vào `dropped`). Bắt đầu lại thì tạo worker mới.
- **Tắt app:** huỷ cả worker studio lẫn worker recap; test chống rò rỉ task của Ngày 2 được mở rộng.
- **Phục vụ test:** `await director.idle()` chờ hàng đợi rỗng.
- **Số đo:** `queued`, `processed`, `dropped`, `lag_ms` (độ trễ từ lúc phát trigger tới lúc phát item), `degraded`. Đưa vào `/healthz` và `status`.

### 14.5. Store của phiên

| Store | Giới hạn | Dùng cho |
|---|---|---|
| `talking_points` | 200 | `hello`, REST, đổi trạng thái |
| `lower_thirds` | 100 | `hello`, REST |
| `commentary` | 500 (mỗi trận khoảng 100) | `hello`, REST |
| `recaps` | 2 (H1, FULL; giữ revision mới nhất) | `hello`, REST |
| `studio_traces` | 300 job | `GET /matches/{id}/studio/{item_id}` |

### 14.6. Trace
- Mỗi job có một `TraceRecorder` với `trace_id = trigger.id`.
- Các span: StudioIntake, mỗi tool call của FactPack, mỗi writer (llm / template), mỗi Guardrail (passed / rejected), StudioPublisher.
- Không ghi prompt toàn văn (giống Ngày 2).
- WebSocket không mang trace; REST trả item kèm trace cho drawer "Agent Trace" của Ngày 4.

---

## 15. API, WebSocket và CLI

### 15.1. REST mới

| Method | Đường dẫn | Mô tả |
|---|---|---|
| `GET` | `/matches/{id}/commentary?since_seq=&mode=&lang=&club=&player=&beginner=` | Danh sách `CommentaryView` theo hồ sơ |
| `GET` | `/matches/{id}/talking-points?since_seq=` | Danh sách `TalkingPoint` (không trace) |
| `GET` | `/matches/{id}/lower-thirds?lang=` | Danh sách `LowerThirdView` |
| `GET` | `/matches/{id}/recap?scope=H1\|FULL&lang=&mode=` | `RecapView` / `409 recap_not_ready` / `404` |
| `GET` | `/matches/{id}/studio/{item_id}` | Item chuẩn kèm trace của job |
| `POST` | `/matches/{id}/talking-points/{tp_id}/status` (P1) | `{status}` → phát `talking_point_status` |
| `GET` | `/healthz` | Thêm khối `studio: {enabled, queued, dropped, lag_ms_p95, degraded}` |

Mã lỗi giữ nguyên quy ước Ngày 2: `404`, `422` (hồ sơ hoặc tham số sai), `409` (chưa tới lúc). Lỗi LLM **không bao giờ** trả `5xx`.

### 15.2. WebSocket
- `/ws/match/{id}` nhận thêm query hồ sơ (mục 11.1).
- Mã đóng kết nối: `4404` (trận không tồn tại, như cũ), `4422` (hồ sơ sai), `4429` (quá giới hạn client hoặc hồ sơ).
- Thứ tự trên một phiên luôn là: `event` → `moment` → (`commentary`, `talking_point`, `lower_third` của trigger đó). Nguyên tắc S8 được test kiểm tra.

### 15.3. CLI
- `pundit studio --scenario S --seed N [--provider mock|ollama|openai|foundry] [--profile mode=casual,lang=vi,club=HAR,beginner=1] [--llm-min-importance 2] [--json out.jsonl] [--report]`
  - Chạy offline cả trận: `detect_all` + `detect_cues`, rồi `StudioService.run` cho từng trigger trên khung nhìn ghim, cùng đường code với chế độ live.
  - In dòng bình luận theo hồ sơ, danh sách talking point và lower-third.
  - `--report` in thêm: số mục mỗi loại, nguồn (llm / template), số bản LLM bị Guardrail chặn, số bị lọt (phải bằng 0), tiêu chí D1–D4, luật "Người mới xem", khoảng lặng dài nhất, độ trễ p50 / p95. Với provider thật: LLM có khả dụng không (giống `pundit eval`).
  - Exit code `1` khi vi phạm ngưỡng.
- `pundit recap --scenario S --seed N [--scope FULL] [--lang vi] [--mode casual] [--provider ...]`.

---

## 16. Chiến lược test (trong lúc xây)

### 16.1. Các tầng test

| Tầng | Thư mục | Nội dung | Ước lượng |
|---|---|---|---|
| Unit | `tests/agents/` | cues, factpack, phrasebank, templates (TP / bình luận / graphics / recap), glossary, rules_library, personalize, studio_guard | ~220 |
| Contract | `tests/contract/` | Schema mới khớp code; ví dụ hợp lệ qua jsonschema | ~8 |
| Property / metamorphic | `tests/property/` | Mục 16.3 | ~30 |
| Workflow | `tests/workflow/` | Workflow studio: luồng chuẩn, ma trận lỗi, MAF = runner asyncio | ~50 |
| API / WS | `tests/api/` | Hồ sơ, cá nhân hoá, store, REST mới, thứ tự, shutdown | ~40 |
| Eval studio | `tests/eval/` | Báo cáo `pundit studio --report` trên 4 kịch bản đạt ngưỡng | ~8 |
| Live (opt-in) | `tests/live/` | Ollama / Foundry, `-m live` | ~4 |
| Perf | `tests/perf/` | Mục 16.5 | ~5 |

### 16.2. Ca bắt buộc theo module
- **cues:**
  - Từng loại cue trên dữ liệu dựng tay.
  - Không sinh cue `shot` cho cú sút đã có moment.
  - `stat_tick` đúng 4 mốc; `added_time` đúng 2.
  - `lull` bảo đảm khoảng lặng ≤ 5 phút.
  - Chạy lô = chạy theo luồng (4 kịch bản × 3 seed); ID không trùng.
- **factpack:** mọi Fact = `compute()` tính lại độc lập trên `view_at(seq)` (oracle); key đúng định dạng; không trùng key; `first_rules` đúng.
- **studio_guard:**
  - Mỗi mã mới có bảng ca đúng / ca sai.
  - **Mutation test** trên mọi template của 4 kịch bản: đổi một số → `UNVERIFIED_NUMBER`; đổi tên cầu thủ sang người không liên quan → `WRONG_ENTITY`; đổi từ kết quả (cứu thua → ghi bàn) → `WRONG_OUTCOME`; tráo đội → `WRONG_ATTRIBUTION`.
  - "sai lầm", "đúng lúc" không bị chặn trong bình luận.
- **templates:**
  - Mọi `kind × giọng × ngôn ngữ` render được và **qua `check_studio`** trên mọi trigger của 4 kịch bản × 3 seed (L4).
  - Đủ ≥ 3 mẫu câu mỗi nhóm; chọn câu tất định.
- **rules_library:** đủ VI và EN, độ dài, không từ cấm, không số của trận; nhóm số liệu khớp registry; bảng kỳ vọng 9.4.
- **personalize:**
  - Bảng chính sách 11.2 (vét cạn mode × lang × có / không CLB × beginner); D1–D4; `framing` chỉ có ở casual khi có CLB.
  - `seen_rules` theo client; hồ sơ sai → lỗi rõ ràng.
- **graphics:** bảng chọn 7.1; JSON hợp lệ; số = display của Fact; `show_at_match_ms` = `match_ms` của trigger; `supersedes` của VAR trỏ đúng graphic bàn thắng.
- **recap:**
  - Bàn thắng, thẻ và số liệu khớp oracle; bàn bị VAR huỷ không có trong danh sách bàn.
  - Độ dài 120–220 từ; `409` trước mốc.

### 16.3. Test metamorphic / property cho studio

| # | Tính chất | Bug bắt được |
|---|---|---|
| S-1 | **Tất định:** chạy cùng trận 2 lần → JSON các item giống hệt (bỏ qua thời gian đo) | Dùng `hash()` hoặc `random`, rò trạng thái |
| S-2 | **Lô = live:** `pundit studio` offline ≡ phiên live 1000x (mock) | Lệch giữa hai đường code, view không ghim |
| S-3 | **Không số lạ:** mọi số trong mọi văn bản đã công bố khớp một Fact của pack (oracle độc lập) | Guardrail có lỗ hổng |
| S-4 | **Ghim view:** Fact của item = `compute()` trên `view_at(trigger.seq)`, kể cả khi replay đã chạy xa | Đọc view hiện tại thay vì view ghim |
| S-5 | **Bất biến ngôn ngữ:** bản VI và EN của cùng một mục dẫn **cùng tập số** | Hai bộ template lệch nhau |
| S-6 | **Đúng cầu thủ, đúng kết quả** (oracle độc lập, dựa trên trường của event): mọi cầu thủ được nêu tên thuộc người liên quan; cú sút không thành bàn không có từ "ghi bàn" | Template hoặc LLM gán sai |
| S-7 | **Đơn điệu theo giọng:** số con số ở analyst ≥ ở casual (D2) | Đảo giọng |
| S-8 | **Nhân quả:** trên luồng WS, item luôn có `n` lớn hơn `n` của event và moment gây ra nó | Phát sớm, sai thứ tự |
| S-9 | **Đổi đội:** dựng trận đối xứng (tráo vai trò nhà / khách) → góc nhìn CLB tráo theo (`own_team` ↔ `opponent`) | Sai `emphasis` |
| S-10 | **Liên tục:** khoảng lặng dài nhất ≤ 5 phút trận trên 4 kịch bản × 3 seed | Thiếu cue |

### 16.4. Ma trận bơm lỗi
Ghép lỗi của `commentator` và `talker`, mỗi bên lấy từ {ok, timeout, error, invalid_json, hallucinate ×1, hallucinate ×2, unsafe, wrong_player, wrong_outcome, wrong_language} (10 × 10 = 100 tổ hợp, chạy trên 1 moment `goal` và 1 cue `shot`). Mỗi tổ hợp phải đạt:
- Luôn có `StudioOutput`, **không exception**; job không vượt `STUDIO_JOB_BUDGET_S` (đồng hồ giả).
- Mọi văn bản đã công bố qua `check_studio`. Bản bị chặn được thay bằng template, kèm cờ `writer_fallback` và span `rejected` → `fallback` trong trace.
- Lỗi của một nhánh không làm đổi output của nhánh kia (so với chạy toàn mock).

Thêm hai test:
- **Breaker studio:** 3 lỗi → mở mạch → `llm_skipped`; sau 60 s (đồng hồ giả) thử lại. Breaker của claim **không bị ảnh hưởng**.
- **Recap:** `recapper` lỗi → giữ `revision = 1`; LLM đúng → `revision = 2`.

### 16.5. Đồng thời và hiệu năng
- **Replay 1000x bật / tắt studio:** thời gian tới `finished` chênh ≤ 20%; không job importance ≥ 2 nào bị bỏ.
- **LLM chậm** (mock trễ 5 s, ngân sách 3 s): mọi item vẫn đăng bằng template; độ trễ phát ≤ ngân sách + 200 ms; breaker mở sau 3 lần.
- **Nhiều client:** 3 hồ sơ × 5 client nhận cùng chuỗi `n`, payload khác theo hồ sơ; một client không đọc không làm chậm replay hay worker.
- **Claim chạy cùng lúc với studio** (20 claim): kết quả claim y như khi tắt studio (C5 của Ngày 2).
- **Ngân sách hiệu năng (mock, trong process):**
  - Job có cả 3 nhánh: p95 < 150 ms. Job chỉ có bình luận: p95 < 60 ms.
  - Cả trận chạy offline: < 15 s.
  - Event → item trên phiên 20x: p95 < 300 ms.

### 16.6. Giữ an toàn cho Ngày 1–2
- Toàn bộ test cũ phải xanh. Những test **được phép sửa có chủ đích** liệt kê ở đây kèm lý do:
  - Tập khoá của `hello`.
  - Các test chờ phiên kết thúc: thêm `await director.idle()` nếu đọc store của studio.
  - Schema `ws_message`.
- Eval claim (`pundit eval`): vẫn 20/20, 0 verdict sai, held-out không giảm.
- Oracle claim 4.652 câu và test metamorphic quan hệ của Guardrail vẫn xanh (Guardrail chỉ đổi phần kiểm tra từ verdict).
- `STUDIO_ENABLED=false`: chạy lại test API / WS của Ngày 2 với cờ tắt, kết quả y như trước (hành vi cũ được giữ nguyên vẹn).

### 16.7. Eval studio (`tests/eval/` + `pundit studio --report`)

| Chỉ số | Mock | LLM thật (offline) |
|---|---|---|
| Số mục bình luận / trận | 60–110 | như mock |
| Talking point | = số moment importance ≥ 2 | như mock |
| Lower-third | đúng bảng 7.1 | như mock |
| Câu bị chặn lọt ra ngoài | **0** | **0** |
| Số bịa / sai cầu thủ / sai kết quả (oracle) | **0** | **0** |
| D1–D4 | 100% (D2: ≥ 80% ở mục có ≥ 2 Fact) | 100% |
| Khoảng lặng dài nhất | ≤ 5 phút trận | — |
| Luật "Người mới xem" | đúng bảng 9.4 | đúng bảng 9.4 |
| LLM không khả dụng | — | ≤ 20% (như `pundit eval`) |
| Tỷ lệ câu LLM được đăng | — | Báo cáo |
| Độ trễ job p50 / p95 | Báo cáo | Báo cáo |

### 16.8. Test live (opt-in)
- `@pytest.mark.live`, provider lấy như Ngày 2.
- Chạy 5 trigger đầu có importance ≥ 2 của `dominant_loser-42` với LLM thật: provider khả dụng, 0 câu bị chặn lọt ra.
- Cả trận chỉ chạy khi `LIVE_STUDIO=1`.

### 16.9. CI
Giữ workflow cũ: `-m "not slow and not perf and not live"` có coverage (≥ 85%, mục tiêu ≥ 93%), perf chạy riêng, không cần secret. Thêm module mới vào test quy tắc import.

---

## 17. Kế hoạch kiểm thử sau khi hoàn thành các chức năng

Chạy theo thứ tự. Mỗi pha có điều kiện qua; chưa qua thì không sang pha sau.

| Pha | Việc | Lệnh / cách làm | Điều kiện qua |
|---|---|---|---|
| **P1. Gate tĩnh** | ruff, ruff format, mypy strict | `uv run ruff check . ; uv run ruff format --check . ; uv run mypy` | Sạch |
| **P2. Hồi quy toàn bộ** | Test mặc định của Ngày 1 + 2 + 3, kèm coverage | `uv run pytest -m "not slow and not perf and not live" --cov=pundit` | 100% xanh; coverage ≥ 93% |
| **P3. Test chậm, hiệu năng và hồi quy hành vi cũ** | Slow sweep (oracle claim, 100 seed); perf; test API / WS Ngày 2 với `STUDIO_ENABLED=false`; `pundit eval` | `uv run pytest -m slow`, `-m perf` | Xanh; eval claim 20/20, 0 sai |
| **P4. Hệ thống, mock** | `pundit studio --report` trên 4 kịch bản × 3 seed; ma trận lỗi; S-1 … S-10 | `uv run python scripts/studio_matrix.py` (in bảng tổng hợp) | Mọi chỉ số đạt bảng 16.7; 0 vi phạm |
| **P5. Live session** | `pundit serve` + script smoke `backend/scripts/smoke_studio.py` (phát triển từ smoke test Day 2 §19.10): 3 hồ sơ (analyst-vi, casual-vi-HAR-beginner, casual-en) cùng xem `dominant_loser-42` ở 20x; claim chạy song song; dừng giữa trận rồi chạy tiếp; tắt server | `python -m uv run python scripts/smoke_studio.py` | Thứ tự S-8 đúng; cùng `n`; payload khác theo hồ sơ; recap H1 / FULL có; không lỗi trong log; không task treo |
| **P6. LLM thật offline** | `pundit studio --provider ollama --llm-min-importance 2 --report --json out.jsonl` trên `dominant_loser-42`. **Đọc lại toàn bộ câu LLM đã đăng và mọi bản nháp bị chặn**, như rà soát Day 2 §19.10: tìm chặn nhầm và lỗ hổng, bổ sung test cho mỗi trường hợp tìm thấy | Lệnh bên cạnh + script `eval_dump` mở rộng | 0 câu bị chặn lọt ra, 0 số bịa, 0 sai cầu thủ / kết quả; mọi phát hiện đã có test |
| **P7. Nghiệm thu thủ công** (20 phút) | Danh sách bên dưới | | Đạt từng bước |
| **P8. Ghi kết quả** | Điền mục 23 (as-built), cập nhật checklist và claude.md; commit local | | Tài liệu khớp code |

**Nghiệm thu thủ công (P7):**
1. `pundit studio --scenario dominant_loser --seed 42 --profile mode=casual,lang=vi,club=HAR,beginner=1`: đọc lướt cả trận, kiểm tra:
   - câu tiếng Việt tự nhiên, đúng thuật ngữ;
   - giải thích việt vị và phạt góc xuất hiện đúng lần đầu;
   - câu mở đầu "đội của bạn" xuất hiện khi Harbor ghi bàn.
2. Cùng lệnh với `mode=analyst`: cùng moment nhưng nhiều số hơn, có xG / momentum. **Chụp màn hình hai bản cho video demo.**
3. `pundit studio --scenario comeback_var --seed 7`: có giải thích VAR ở bàn bị huỷ (khoảng 36'), có lower-third "VAR: KHÔNG CÔNG NHẬN BÀN THẮNG" thay graphic của bàn đó.
4. `pundit studio --scenario red_card_swing --seed 7`: thẻ đỏ 52' có talking point, player card và lời giải thích thẻ đỏ.
5. `pundit recap --scenario dominant_loser --seed 42 --lang vi --mode casual`, đọc to thử: khoảng 1–2 phút.
6. `$env:MOCK_LLM_FAULTS = "commentator:wrong_outcome:1,talker:hallucinate"` rồi chạy `pundit studio ... --report`: báo cáo có câu LLM bị chặn và được thay bằng template, 0 bị lọt. **Đây là cảnh demo Guardrail của Ngày 3.**
7. `pundit serve` + mở 2 tab WebSocket (analyst, casual) xem cùng một bàn thắng.
8. Đọc lại 15 lời giải thích trong thư viện "Người mới xem" (mục 9.1) để duyệt nội dung bằng mắt.

---

## 18. Quy trình thực hiện (thứ tự + gate)

| # | Bước | Thời lượng | Đầu ra | Gate |
|---|---|---|---|---|
| 0 | Spike MAF fan-out / fan-in + sửa docstring cũ | 0:45 | Ghi chú ở §14.2 và §23; quyết định dùng MAF hay runner asyncio | Workflow 3 nhánh + fan-in chạy 10 lần không lẫn trạng thái |
| 1 | Contracts + schema + settings + role bơm lỗi | 1:00 | `contracts.py`, schema, `settings.py`, `faults.py` | Contract test, test settings |
| 2 | CueDetector | 1:00 | `cues.py`; chốt bảng 4.3 và 9.4 bằng dữ liệu thật | Bảng luật; lô = luồng; khoảng lặng ≤ 5' |
| 3 | FactPack | 1:00 | `factpack.py` + span tool | Oracle Fact = compute tại seq |
| 4 | Guardrail studio | 1:15 | `studio_guard.py`, mã mới, cờ từ verdict | Bảng ca đúng / sai; test Ngày 2 xanh |
| 5 | Thuật ngữ + thư viện luật + câu mở đầu theo CLB | 0:45 | `glossary.py`, `rules_library.py` | Test nội dung tĩnh |
| 6 | Template: talking point, bình luận, graphics, recap | 2:30 | `talking_points.py`, `commentary.py`, `graphics.py`, `recap.py`, `phrasebank.py` | Mọi template của 4 kịch bản × 3 seed qua `check_studio`; mutation test; S-5 |
| 7 | Personalization | 1:15 | `personalize.py` | Bảng chính sách; D1–D4 |
| 8 | LLM: port, mock, prompt, DTO, adapter | 1:30 | `llm/*` | Test DTO / prompt; mock = template |
| 9 | Studio steps + workflow + service + recap service | 1:45 | `orchestration/studio_*`, `recap_service.py` | Ma trận lỗi 100 tổ hợp; MAF = runner asyncio |
| 10 | Director + session + hub + WS + REST | 2:00 | `api/*` | Test API / WS, đồng thời, shutdown, S-8 |
| 11 | CLI `studio` / `recap` + báo cáo eval studio | 0:45 | `cli.py` | Báo cáo mock đạt bảng 16.7 |
| 12 | Kiểm thử sau khi hoàn thành (mục 17, P1–P8) | 1:30 | Mục 23, checklist, claude.md | DoD tầng A (+ B với Ollama) |

**Tổng khoảng 17 giờ.** Nếu trễ, cắt theo thứ tự:
1. "Một chỉ số" (P2).
2. GraphicsDesigner bằng LLM (P1, giữ bảng rule).
3. Player tracker (P1, giữ `emphasis`).
4. Recap EN bằng LLM (giữ template EN).
5. REST đổi trạng thái talking point (P1).
6. Bản recap LLM (`revision = 2`): chỉ giữ template.
7. Cue `lull` (giữ `stat_tick`).

**Không bao giờ cắt:**
- Bước 1–7 (phần lõi tất định).
- Guardrail studio, đường template của mọi sản phẩm.
- Personalization analyst / casual / ngôn ngữ / CLB.
- "Người mới xem", bình luận VI, recap VI, các message WS mới.
- Test tất định (S-1, S-2), test ma trận lỗi, test hồi quy Ngày 1–2.

---

## 19. Bảng chống bug

| Loại bug | Ví dụ | Phòng tránh (thiết kế) | Phát hiện (test) |
|---|---|---|---|
| Số bịa trong bình luận | "Harbor có 18 cú sút" khi thật là 15 | FactPack + `check_studio` + template dự phòng | Mutation test, S-3, ma trận lỗi |
| Sai cầu thủ | Bàn thắng của Calder bị ghi cho Marsh | `WRONG_ENTITY` theo `involved_players` | Mutation test, S-6 |
| Sai kết quả tình huống | Cú sút bị cứu thua lại được tả là "ghi bàn" | `WRONG_OUTCOME` với bảng từ 12.3 | Mutation test, S-6, `wrong_outcome` |
| Câu đánh giá không có căn cứ | "Kestrel đang chơi tốt hơn" | `UNGROUNDED` cho câu đánh giá | Bảng ca, eval LLM offline |
| Lẫn ngôn ngữ | Câu "tiếng Việt" chứa nửa câu tiếng Anh | `WRONG_LANGUAGE` | `wrong_language`, D4 |
| Chặn nhầm câu đúng trong bình luận | "Một sai lầm của hàng thủ" bị coi là nhãn verdict | Chỉ kiểm tra từ verdict khi có verdict | Bảng ca |
| Không tất định | Câu khác nhau giữa hai lần chạy | `crc32`, không `random` / `hash()`; ID từ trigger | S-1, S-2 |
| Studio làm nghẽn replay | Replay đứng hình khi LLM chậm | Hàng đợi `put_nowait`, worker riêng, ngân sách | Test 1000x bật / tắt studio, LLM chậm |
| Item sai thứ tự hoặc phát trước trigger | Bình luận bàn thắng đến trước event bàn thắng | Một worker; publisher gộp theo thứ tự cố định; job tạo sau khi đã phát | S-8 |
| Số liệu xê dịch | Talking point dùng số của phút sau | `view_at(trigger.seq)` | S-4 |
| Cá nhân hoá làm đứt chuỗi `n` | Client casual thấy "mất" message | Không bỏ message theo hồ sơ; `relevance = low` | Test 2 hồ sơ cùng `n` |
| Spam lời giải thích luật | Giải thích việt vị mỗi lần có việt vị | `seen_rules` theo client; chỉ thẻ đỏ / phạt đền nhắc lại bản ngắn | Test `seen_rules` |
| Thiếu lời giải thích | Không giải thích VAR ở bàn bị huỷ | Bảng luật 4.2 + `first_rules` | Bảng kỳ vọng 9.4 |
| Timeout của studio làm hỏng claim | Breaker chung mở vì ngân sách 3 s của studio | Breaker riêng cho studio | Test breaker độc lập |
| Hồ sơ độc hại / quá nhiều hồ sơ | Query lạ, 1.000 hồ sơ khác nhau | Parse chặt, `4422`; giới hạn 64 client / 32 hồ sơ (`4429`) | Test API |
| Recap sai bàn | Đếm cả bàn bị VAR huỷ | Dùng danh sách bàn đã loại bàn bị huỷ (như `select()` Ngày 1) | Test `comeback_var` |
| Overlay lệch thời điểm | Graphic hiện sai lúc khi replay 20x | `show_at_match_ms` + `duration_ms` ở 1x; UI quy đổi theo tốc độ | Test JSON + quy ước ghi trong hợp đồng |
| Bộ nhớ phình | Store không giới hạn | Giới hạn ở bảng 14.5 | Test giới hạn |
| Task treo khi tắt app | Worker studio chạy ngầm | Huỷ worker trong `stop` / shutdown | Test shutdown mở rộng |
| Template trượt Guardrail | Lối thoát cuối cùng bị chặn | Test mọi template trên 4 kịch bản × 3 seed | Test template |
| Quá nhiều câu cho TTS | Ngày 5 đọc không kịp | `speak`, `importance`, `excitement` | Báo cáo số câu `speak` mỗi trận |
| Gãy client Ngày 2 | Client cũ gặp type mới | Hợp đồng: bỏ qua type không biết; `v` giữ 1; hồ sơ mặc định | Test client chỉ đọc type cũ |

---

## 20. Rủi ro và phương án

| Rủi ro | Khả năng | Phương án |
|---|---|---|
| MAF 1.22 không hỗ trợ fan-in như mong đợi | Trung bình | Spike ở bước 0; nối tuần tự trong workflow, hoặc dùng runner asyncio (L6 bảo đảm logic không phải sửa) |
| Ngày 3 quá tải (khoảng 17 giờ) | Cao | Thứ tự cắt ở mục 18; lõi tất định làm trước, LLM làm sau |
| `qwen2.5:3b` viết tiếng Việt kém, câu bị chặn nhiều | Cao | Template là đường chính; đo tỷ lệ được đăng; Foundry ở Ngày 5 |
| Ollama quá chậm cho real-time | Chắc chắn | Breaker studio + ngân sách; LLM thấy rõ ở recap và chế độ offline |
| Template nghe máy móc, lặp | Trung bình | Ngân hàng ≥ 3 mẫu mỗi nhóm, chọn bằng `crc32`; có bước duyệt bằng mắt (P7) |
| Guardrail chặn nhầm câu đúng của LLM | Trung bình | Bài học Day 2 §19.10: đọc lại mọi lần chặn ở P6, mỗi phát hiện thêm một test |
| Nội dung thư viện luật sai | Thấp | Đối chiếu Luật IFAB; duyệt bằng mắt ở P7; định nghĩa số liệu khớp registry (có test) |
| Hiệu năng Guardrail khi nhiều bản văn bản | Trung bình | Chỉ 4 bản bình luận + 6 câu talking point mỗi job; cache `find_entities`; đo bằng test perf |
| Azure chưa có tới Ngày 5 | Trung bình | Mọi thứ chạy được với template; demo vẫn đủ luồng; LLM thật chứng minh bằng Ollama offline |

---

## 21. Hợp đồng cho Ngày 4+

| Hợp đồng | Bên dùng | Quy tắc thay đổi |
|---|---|---|
| WS `talking_point`, `lower_third`, `commentary`, `recap`, `talking_point_status`; query hồ sơ | Studio Dashboard, overlay, Fan VN Player (Ngày 4) | Chỉ thêm trường; nâng `v` khi đổi hoặc xoá trường |
| `LowerThirdView` (`match_clock`, `show_at_match_ms`, `duration_ms`, `priority`, `supersedes`, màu đội, `synthetic`) | Trang `/overlay`, OBS (Ngày 4) | Chỉ thêm trường |
| `CommentaryView.speak`, `excitement`, `language`, `explanation` | TTS + SSML (Ngày 5) | Ngày 5 thêm trường `audio_url` |
| `RecapView` + `GET /recap` | Fan VN Player (Ngày 4), recap audio (Ngày 5) | Chỉ thêm trường |
| `GET /matches/{id}/studio/{item_id}` (item + trace) | Drawer "Agent Trace" (Ngày 4), App Insights (Ngày 5) | Chỉ thêm trường |
| Port `TalkingPointWriterPort`, `CommentatorPort`, `RecapWriterPort` | Gắn Foundry (Ngày 5): chỉ đổi cấu hình | Không đổi chữ ký |
| `FactPack` + catalog tool | MCP server (Ngày 6) dùng lại `plan()` / catalog | Không đổi tên tool |

---

## 22. Quyết định đã chốt (22/10/2026)

| # | Quyết định | Chốt |
|---|---|---|
| 1 | Ngôn ngữ talking point | ✅ **Anh + Việt**, cùng một bộ số liệu; LLM viết cả hai trong một lần gọi |
| 2 | Mật độ bình luận | ✅ **Moment + sự kiện chính** (cú sút, phạt góc, việt vị, VAR, thẻ, thay người) + `stat_tick` mỗi 15' + cue `lull` để khoảng lặng ≤ 5'; khoảng 65–105 mục mỗi trận |
| 3 | LLM khi chưa có Foundry | ✅ **Template trước, LLM trong ngân sách** (talking point 3 s, bình luận 2,5 s); recap và chế độ offline có ngân sách dài; mock mặc định cho dev / CI |
| 4 | Nơi cá nhân hoá | ✅ **Server, theo từng kết nối** (query hồ sơ trên WS); render một lần mỗi khoá hồ sơ; giữ nguyên chuỗi `n` |
| 5 | Tên message bình luận | ✅ `commentary` (có trường `language`), thay cho `vn_commentary` của checklist, vì luồng có cả EN |
| 6 | Hồ sơ mặc định | ✅ `analyst + vi`; client Ngày 2 không cần đổi |
| 7 | LLM viết bản nào của bình luận | ✅ `vi/analyst` + `vi/casual` trong một lần gọi; EN do template (LLM EN là P1) |
| 8 | Góc nhìn CLB | ✅ Câu mở đầu tĩnh, chỉ ở giọng casual; analyst giữ trung lập |
| 9 | Lời giải thích luật | ✅ Văn bản tĩnh, trường `explanation` riêng, bản đầy đủ ở lần đầu gặp của mỗi client |
| 10 | Chữ trên lower-third | ✅ Chỉ dùng ngân hàng câu tĩnh (an toàn khi lên sóng); LLM chỉ chọn chỉ số (P1) |
| 11 | Breaker | ✅ Studio có breaker riêng, tách khỏi claim |
| 12 | Recap | ✅ Phát template ngay (`revision 1`), bản LLM sau (`revision 2`) nếu qua Guardrail |

---

## 23. As-built (điền sau khi hoàn thành)

- 23.1. Kết quả spike MAF fan-out / fan-in.
- 23.2. Khác biệt so với thiết kế (nếu có) và lý do.
- 23.3. Bảng số lượng thực tế (thay cho ước tính ở 4.3) và bảng luật "Người mới xem" thực tế (thay cho 9.4).
- 23.4. Kết quả kiểm thử theo mục 17 (P1–P8): số test, coverage, báo cáo `pundit studio --report` của 4 kịch bản, ma trận lỗi, hiệu năng.
- 23.5. Kết quả chạy Ollama offline và các phát hiện khi đọc lại câu LLM.
- 23.6. Việc còn lại cho Ngày 4–5.
