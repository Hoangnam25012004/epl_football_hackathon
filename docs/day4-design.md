# Detailed Design — Ngày 4 (24/10): Studio Dashboard · Overlay lower-third · Fan VN Player · Màn so sánh

> Liên quan: [claude.md](../claude.md) · [checklist.md](../checklist.md) (mục Ngày 4) · [day2-design.md](day2-design.md) (API, WebSocket) · [day3-design.md](day3-design.md) (§15 API studio, §21 hợp đồng cho Ngày 4+, §23 as-built)
> Phiên bản: v1.0 (22/10/2026) · Người thực hiện: 1 người · Frontend: Next.js 16 (static export) + React 19 + TypeScript 5.9 + Tailwind 4 · Backend: Python 3.12 (chỉ thêm)
> Chiến lược: **UI là màn chiếu của server.** Mọi con số và câu chữ trên màn hình đều do server gửi (đã qua Stats Tools và Guardrail); UI không tự tính số, không tự viết câu. Chưa gắn Foundry: backend chạy mock cho dev, test và E2E; câu chữ thật của Ollama chỉ dùng làm fixture để kiểm tra bố cục.

---

## 0. Mục tiêu, phạm vi và Definition of Done

Ngày 1–3 đã có dữ liệu tổng hợp, kiểm chứng claim, moment và studio crew (talking point, lower-third, bình luận, recap, cá nhân hoá) qua REST + WebSocket. Ngày 4 dựng các màn hình để người dùng thật dùng được những thứ đó, và thêm vài endpoint backend còn thiếu.

| Màn hình | Người dùng | Nội dung chính | Ưu tiên |
|---|---|---|---|
| **Studio Dashboard** `/studio/` | Bình luận viên, biên tập viên | Scoreboard + điều khiển replay; feed sự kiện; ô claim + verdict; hàng đợi talking point (duyệt / ghim / bỏ); điều khiển lower-third lên sóng; drawer **Why / Evidence / Agent Trace** | P0 (timeline momentum, sơ đồ sân, bảng chỉ số: P1) |
| **Overlay** `/overlay/` | OBS / vMix (Browser Source) | Lower-third nền trong suốt, animation vào / ra; lên sóng tự động theo priority và theo lệnh Take / Clear / Hold của Studio; JSON có mốc thời gian trận | P0 (thử trong OBS: P1) |
| **Fan VN Player** `/fan/` | Fan, mặc định tiếng Việt | Chọn trận; chọn hồ sơ (Analyst / Casual, ngôn ngữ, CLB, Người mới xem); bình luận real-time dạng chữ; recap | P0 (Player focus: P1; "một chỉ số": P2, backend đã có) |
| **So sánh** `/compare/` | Video demo, giám khảo | Hai hồ sơ cạnh nhau trên cùng moment (mặc định Analyst EN ‖ Casual VI) | Thêm (đã chốt) |
| **Trang chọn trận** `/` | Mọi người | Chọn kịch bản + seed, mở các màn trên, xem phiên đang chạy | P0 |

**Definition of Done** (ba tầng như Ngày 2–3):

| Tầng | Điều kiện | Kiểm tra bằng |
|---|---|---|
| **A — mock, local** (bắt buộc trong Ngày 4) | 1. Chạy `pundit serve --ui ../frontend/out` rồi mở `http://localhost:8000/` là đi được đủ luồng của checklist: **claim → verdict (màu + số) → drawer; talking point → duyệt / ghim / bỏ; lower-third lên overlay (tự động và theo Take / Clear); bình luận tiếng Việt theo hồ sơ, bật "Người mới xem" thấy giải thích luật**; màn so sánh hiện hai câu khác nhau cho cùng moment. 2. Mọi màn có nhãn **Synthetic data** (test tự động). 3. Tắt rồi bật lại backend giữa trận: UI không crash, tự kết nối lại và bắt kịp (`hello` mới). 4. Hai tab Studio cùng trận thấy cùng trạng thái talking point và lower-third trong < 1 s. 5. Không lỗi console; 0 vi phạm axe mức serious / critical. 6. Toàn bộ test Ngày 1–3 vẫn xanh (1764 test); test Ngày 4 xanh; gate tĩnh (ruff, mypy, tsc, eslint, prettier) sạch | `pytest -m "not live"`, `npm run check`, `npm run test`, `npm run e2e` (§12) |
| **B — câu chữ của LLM thật** | Bố cục không vỡ với câu dài thật của `qwen2.5:3b` (fixture ghi từ lượt Ollama của Ngày 3): không tràn chữ, không cắt mất số, lower-third vẫn trong khung | Component test với fixture B (§11.2) |
| **C — Foundry + giọng đọc** (Ngày 5) | Overlay và Fan Player chạy trên URL public; Fan Player phát audio theo `speak` / `excitement` | Ngày 5 |

**Ngoài phạm vi Ngày 4:** audio TTS (Ngày 5; Ngày 4 chỉ chừa giao diện `AudioQueue`), Dockerfile và deploy (Ngày 5), đăng nhập, Speech-to-Text, MCP (Ngày 6), sửa câu chữ talking point trên UI, tua lùi giữa trận (chỉ có "chạy lại từ phút X"), tách phiên riêng cho từng người xem trên bản public (quyết định ở Ngày 5).

### 0.1. Đối chiếu checklist Ngày 4 → thiết kế → test

| Mục checklist | Ưu tiên | Thiết kế | Test |
|---|---|---|---|
| Scoreboard + đồng hồ trận + nút điều khiển replay (play / pause / tốc độ) | P0 | §3.1, §5.2 | backend §3.9; E1, E2 |
| Feed sự kiện real-time | P0 | §5.3 | E2 |
| Ô nhập claim + verdict (màu theo kết quả) + số liệu | P0 | §5.4 | E3, E4 |
| Hàng đợi talking points: duyệt / bỏ / ghim | P0 | §5.5 | E5 |
| Drawer Why / Evidence / Agent Trace | P0 | §5.9 | E3 |
| Timeline momentum | P1 | §3.3, §5.8 | component + snapshot hình |
| Sơ đồ sân hiển thị vị trí các event làm bằng chứng | P1 | §3.5, §5.9 | component + E3 |
| Trang `/overlay` nền trong suốt, nhận JSON qua WS, animation vào / ra | P0 | §6 | E6, E7, F6 |
| JSON overlay có `match_clock`, `show_at`, `duration` | P0 | §6.5 (đã có từ Ngày 3: `match_clock`, `show_at_match_ms`, `duration_ms`) | E6 |
| Thử đưa vào OBS | P1 | §6.6 | M3 |
| Chọn trận, bình luận tiếng Việt dạng text real-time | P0 | §7, §9 | E8 |
| Công tắc "Người mới xem" | P0 | §7.2 | E8 |
| Màn chọn hồ sơ: Analyst / Casual, ngôn ngữ, CLB | P0 | §7.1 | E8, E9 |
| Màn Player focus | P1 | §7.3 | component |
| Nhãn "Synthetic data" trên mọi màn hình | P0 | U5, §4.8, §6.1 | E1–E12 (mỗi trang) |
| *Hoàn thành khi:* chạy local thấy đủ luồng claim, verdict, talking point, lower-third, bình luận tiếng Việt | — | §0 tầng A | M1 |

---

## 1. Nguyên tắc thiết kế UI

| # | Nguyên tắc | Áp dụng |
|---|---|---|
| U1 | **UI không tự tính số** | Chỉ hiển thị `display`, `text`, `clock_display`, `display_minute` server gửi. Không cộng dồn thống kê, không làm tròn, không đổi đơn vị ở client. Biểu đồ vẽ từ `value` server tính (timeline, thanh so sánh), còn chữ trên biểu đồ dùng `display`. Hệ quả: mọi con số trên màn đều truy được về Fact và event ID ("không có bằng chứng thì không nói") |
| U2 | **Server là nguồn sự thật, client là phép chiếu** | `state = reduce(hello + messages)`; `hello` thay toàn bộ state. Thao tác của người dùng (duyệt talking point, Take lower-third, pause) gọi REST rồi **chờ message WS** mới coi là xong. Trong lúc chờ thì hiện "đang gửi"; khi lệch, server thắng |
| U3 | **Luồng có số thứ tự** | Kiểm tra `n` tăng đúng 1 mỗi message. Trùng `n` thì bỏ. Hở `n` thì đóng và kết nối lại để nhận `hello` mới (server không có lệnh "gửi lại"). Mọi item khử trùng theo `id` |
| U4 | **Không bao giờ crash trước giám khảo** | Error boundary cho từng panel. Message có `type` lạ bị bỏ qua (quy ước Ngày 3). JSON hỏng bị đếm rồi bỏ. REST lỗi hiện thành toast. Mất kết nối hiện banner và tự kết nối lại. Không `throw` trong render |
| U5 | **Synthetic data trên mọi màn hình** | `SyntheticBadge` nằm trong layout gốc (mọi trang) và trong chính lower-third của overlay; test E2E kiểm tra từng trang |
| U6 | **Hợp đồng sinh từ schema** | Type TypeScript sinh từ `packages/schemas/*.schema.json`, không viết tay; CI sinh lại và báo lỗi nếu lệch |
| U7 | **Tất định để test** | Không dùng `Date.now()` trong render; đồng hồ trận lấy từ server. Reducer, bộ lập lịch overlay, căn hàng màn so sánh, định dạng phút đều là hàm thuần có unit test. Thời gian được tiêm vào component |
| U8 | **Ngôn ngữ giao diện tách khỏi ngôn ngữ nội dung** | Ngôn ngữ UI (nhãn, nút) theo nút chuyển. Ngôn ngữ nội dung theo hồ sơ người xem trên WS. Studio / overlay mặc định UI tiếng Anh; Fan mặc định tiếng Việt |
| U9 | **Nhẹ và mượt ở tốc độ cao** | Gom cập nhật store theo khung hình (`requestAnimationFrame`); danh sách có giới hạn; trang overlay không tải code của Studio |
| U10 | **Backend chỉ thêm** | Mọi thay đổi trên code Ngày 1–3 là thêm trường, thêm endpoint, thêm loại message. `STUDIO_ENABLED=false` vẫn ra đúng app Ngày 2. Schema đã xuất chỉ được thêm trường |

---

## 2. Kiến trúc

### 2.1. Luồng tổng thể

```
Trình duyệt (bản static export của Next.js, FastAPI phục vụ tại "/")
  /            Chọn trận        REST: /scenarios, /matches, /healthz
  /studio/     Studio Dashboard WS (analyst, ngôn ngữ UI) + REST: claim, trạng thái talking point,
                                on-air, điều khiển replay, stats, timeline, events
  /overlay/    Overlay          WS (lang=en|vi): lower_third, on_air, status
  /fan/        Fan VN Player    WS (hồ sơ fan) + REST: recap
  /compare/    So sánh          2 × WS (hai hồ sơ)
        │  cùng origin:  http(s)://host/...   và   ws(s)://host/ws/match/{id}?<hồ sơ>
        ▼
FastAPI  (pundit serve --ui ../frontend/out)
  routes.py (+ endpoint Ngày 4) · ws.py · StaticFiles(html=True) mount cuối cùng tại "/"
  MatchSession: replay (+ pause / speed) · Hub (n, resync) · StudioDirector · OnAir
```

- **Production và E2E:** `npm run build` tạo `frontend/out/`; FastAPI phục vụ cả UI lẫn API trên cùng origin (đã chốt). Không cần CORS, WS cùng host, một container ở Ngày 5.
- **Dev:** `npm run dev` (Next, cổng 3000, hot reload) chạy song song với `pundit serve` (cổng 8000). Frontend gọi `NEXT_PUBLIC_API_BASE=http://localhost:8000`; CORS của backend đã cho phép `http://localhost:3000` từ Ngày 2. Không dùng `rewrites` vì static export không hỗ trợ.
- Trang dùng **query string** (`/studio/?match=dominant_loser-42`) thay cho route động, vì static export không sinh được trang cho ID chưa biết lúc build.

### 2.2. Thư mục frontend

```
frontend/
  package.json · package-lock.json · next.config.ts · tsconfig.json · eslint.config.mjs
  postcss.config.mjs · vitest.config.ts · playwright.config.ts · .prettierrc
  scripts/gen-types.mjs           # packages/schemas → src/lib/contracts/*.ts
  scripts/bundle-size.mjs         # đo JS gzip của từng trang (F7)
  src/app/
    layout.tsx                    # font, theme, SyntheticBadge, ToastHost, I18nProvider
    page.tsx                      # "/" chọn trận
    studio/page.tsx · overlay/page.tsx · fan/page.tsx · compare/page.tsx
    globals.css                   # Tailwind 4 + token CSS
  src/lib/
    contracts/                    # SINH TỰ ĐỘNG, không sửa tay
    ws-types.ts                   # map WsType → payload (viết tay, có test type)
    api.ts                        # REST client: timeout, lỗi có kiểu
    socket.ts                     # MatchSocket: kết nối lại, kiểm tra n, mã đóng 44xx
    profile.ts                    # Profile ↔ query, kiểm tra theo đội hình
    clock.ts                      # formatMinute (bản sao format_minute, có test vàng)
    pitch.ts                      # toạ độ event → sơ đồ sân
    i18n/{index.ts,en.ts,vi.ts}   # chữ giao diện
    overlay-scheduler.ts          # bộ lập lịch lower-third (hàm thuần)
    compare.ts                    # căn hàng hai hồ sơ (hàm thuần)
    verdict.ts · events.ts · color.ts
  src/store/
    match-store.ts                # state + reduce(message) + selector
    frame-batcher.ts              # gom message theo khung hình
  src/components/
    common/   SyntheticBadge, ConnectionBanner, ErrorBoundary, Toast, Scoreboard, TeamChip,
              LangToggle, Drawer, Tabs, EmptyState
    studio/   ReplayControls, EventFeed, ClaimBox, VerdictCard, TalkingPointQueue,
              LowerThirdMonitor, CommentaryPreview, StatsPanel, MomentumTimeline, PitchMap,
              ItemDrawer, TraceGantt
    overlay/  LowerThird (4 template), OverlayStage
    fan/      ProfileSetup, CommentaryFeed, CommentaryCard, RuleExplanation, PlayerTracker,
              RecapPanel, AudioQueue (chỉ giao diện)
    compare/  CompareColumns
  tests/
    unit/ · component/ · contract/ · e2e/ · e2e-mock/ · fixtures/ (JSONL ghi từ backend, §11.2)
```

### 2.3. File backend mới và đổi (chỉ thêm)

| File | Thay đổi |
|---|---|
| `api/sessions.py` | `pause()`, `resume()`, `set_speed()`; trạng thái `paused`; `OnAir` của phiên; `hello` thêm `on_air`; báo client khi phiên bị thay do restart |
| `api/routes.py` | Endpoint §3.1–3.6 |
| `api/onair.py` (mới) | Model và luật chuyển trạng thái on-air |
| `api/panels.py` (mới) | Tính `timeline`, `stats`, tra `events` trên khung nhìn hiện tại (dùng Stats Tools; cache theo `as_of_seq`) |
| `api/app.py` | Mount UI tĩnh khi có `UI_DIR`; header cache |
| `agents/contracts.py` | `WsType` thêm `on_air`; model `OnAirState`, `TimelineView`, `StatsPanel`, `EventsPage`, `ScenarioInfo` |
| `llm/settings.py`, `cli.py` | `UI_DIR` / `pundit serve --ui PATH`; `pundit schema` xuất thêm schema mới |
| `engine/scenarios/catalog.py` | Câu claim mẫu VI / EN cho từng kịch bản |
| `scripts/record_ws.py` (mới) | Ghi luồng WS của một trận thành JSONL làm fixture cho frontend |

### 2.4. Dependency frontend

Chốt phiên bản chính xác lúc cài (không dùng `^`), commit `package-lock.json`, CI dùng `npm ci`.

| Gói | Phiên bản | Ghi chú |
|---|---|---|
| `next` | 16.4.x | `output: "export"` |
| `react`, `react-dom` | 19.x | Bản đi kèm Next 16.4 |
| `typescript` | 5.9.3 | Chưa dùng TS 7 (bản native mới, tooling của Next / ESLint chưa chắc theo kịp) |
| `tailwindcss`, `@tailwindcss/postcss` | 4.x | Token qua CSS variable |
| `zustand` | 5.x | Một store cho mỗi kết nối |
| `vitest`, `@testing-library/react`, `@testing-library/user-event`, `jsdom` | 5.x / 16.x | Unit + component |
| `fast-check` | 4.x | Property test cho reducer và scheduler |
| `@playwright/test`, `@axe-core/playwright` | 1.64.x / 4.x | E2E + a11y; ở local dùng Edge / Chrome có sẵn (`channel`) |
| `json-schema-to-typescript`, `ajv`, `ajv-formats` | 16.x / 8.x | Sinh type; kiểm tra fixture theo schema |
| `eslint`, `eslint-config-next`, `prettier` | 10.x / 16.x / 3.x | |

- Không dùng thư viện biểu đồ hay animation: timeline, sơ đồ sân và Gantt vẽ bằng SVG thuần; animation bằng CSS.
- Font **Be Vietnam Pro** (giấy phép OFL, đủ dấu tiếng Việt) qua `next/font/google`: tải về lúc build và đóng gói cùng bản tĩnh.
- **Đã kiểm tra trên máy dev (22/10):**
  - Node 24.18, npm 11.16, truy cập được npm registry.
  - Binary native của esbuild và SWC của Next chạy được, dù Windows Application Control đang chặn `python.exe` của venv.
  - npm 11 chặn script `postinstall` mặc định. Không gói nào ở trên cần script đó: esbuild và SWC lấy binary qua optional dependency.
  - Máy có sẵn Edge và Chrome nên Playwright không cần tải trình duyệt ở local. OBS chưa cài.

---

## 3. Thay đổi backend (chỉ thêm)

### 3.1. Điều khiển replay: pause, resume, tốc độ

Hiện có `POST /matches` (tạo phiên; `speed`, `from_minute`, `restart`), `/start` và `/stop`. `stop` huỷ cả job studio đang chờ, nên không dùng làm "pause".

| Method | Đường dẫn | Hành vi |
|---|---|---|
| `POST` | `/matches/{id}/pause` | Huỷ **chỉ** task replay; StudioDirector không bị đụng (job đang chờ vẫn chạy xong và vẫn được phát). `status = paused`, phát message `status` |
| `POST` | `/matches/{id}/resume` | Tạo lại task replay từ `len(state)`; mốc thời gian neo lại tại event kế tiếp. Idempotent khi đang `running` |
| `POST` | `/matches/{id}/speed` `{speed}` | `0 < speed ≤ 1000`. Đang chạy thì pause → đổi → resume, không mất event. Đang dừng thì chỉ lưu |
| `POST` | `/matches` (đã có) | "Chạy lại từ phút X" = `restart: true, from_minute: X` |

- `SessionStatus` và `MatchInfo.status` thêm giá trị `paused` (thêm vào `Literal`; client cũ chỉ hiển thị).
- Payload của message `status` thêm `speed` và `restarted` (bool).
- **Restart không làm client "đứng hình":**
  - Trong `SessionManager.create(restart=True)`: dừng phiên cũ, đăng ký phiên mới, **rồi** mới phát `status {session: "stopped", restarted: true}` trên hub của phiên cũ.
  - Client thấy cờ này thì kết nối lại sau 300 ms và nhận `hello` của phiên mới.
  - Thứ tự "đăng ký phiên mới trước khi báo" bảo đảm client kết nối lại không rơi vào phiên cũ. Có test cho thứ tự này.
- **Bất biến (có test):**
  - Pause, resume và đổi tốc độ không làm mất, lặp hay đảo thứ tự event.
  - `n` vẫn liên tục.
  - Studio item phát trong lúc pause vẫn đúng nhân quả (S8 của Ngày 3).
- Huỷ task chỉ xảy ra ở điểm `await clock.sleep` trong `replay()`, nên `apply()` (đồng bộ) không bao giờ bị cắt giữa chừng.

### 3.2. On-air: điều khiển lower-third (đã chốt "tự động + biên tập viên can thiệp")

```python
class OnAirState(Contract):
    mode: Literal["auto", "manual"] = "auto"
    item_id: str | None = None   # lower-third đang được Take (manual)
    hold: bool = False           # giữ món đang hiện, không cho tự đổi
    revision: int = 0            # tăng mỗi lần đổi; client bỏ message có revision cũ hơn
    at_match_ms: int = 0         # thời điểm trận lúc đổi
```

| Method | Đường dẫn | Body | Hiệu lực |
|---|---|---|---|
| `GET` | `/matches/{id}/on-air` | — | `OnAirState` hiện tại |
| `POST` | `/matches/{id}/on-air` | `{action: "take", item_id}` | `mode=manual`, đặt `item_id`; `404` nếu lower-third không tồn tại |
| | | `{action: "clear"}` | Bỏ món đang hiện: ở manual thì `item_id=null` (màn trống tới khi Take hoặc Auto); ở auto thì bỏ qua món đang hiện |
| | | `{action: "hold"}` / `{action: "release"}` | Bật / tắt `hold` |
| | | `{action: "auto"}` | Về `mode=auto`, `item_id=null`, `hold=false` |

- Mỗi thay đổi tăng `revision` và phát message WS mới **`on_air`** (payload `OnAirState`, giống nhau cho mọi client). `hello` có thêm `on_air`.
- Lập lịch tự động chạy **ở client**, bằng một hàm thuần dùng chung cho overlay và khung theo dõi của Studio (§6.3). Cùng luồng message thì ra cùng thứ tự. Overlay trong OBS vẫn chạy khi không ai ngồi Studio.
- Server chỉ giữ phần do con người quyết định (Take / Hold / Clear), nên không cần timer ở server.

### 3.3. Timeline momentum (P1)

`GET /matches/{id}/timeline?window=10` → `TimelineView`:

```
{ as_of_seq, window_minutes, home, away,
  points:  [{minute, value, display}],                         # value ∈ [-1, 1], góc nhìn đội nhà
  markers: [{moment_id, kind, minute, display_minute, team}] }  # bàn thắng, VAR huỷ, thẻ đỏ, hết hiệp
```

- Dùng `stats.tactical.momentum_series` (có từ Ngày 1) trên khung nhìn hiện tại; `display` dạng `+0.42`.
- Cache theo `as_of_seq` (mỗi phiên chỉ có một writer).
- Chỉ tính tới event đã áp dụng, không bao giờ lộ tương lai.

### 3.4. Bảng chỉ số (P1)

`GET /matches/{id}/stats?period=FULL|H1|H2` → `StatsPanel`:

- Mỗi hàng: `{metric, label_vi, label_en, home: {value, display}, away: {value, display}, evidence_count}`.
- Các chỉ số: `possession`, `shots`, `shots_on_target`, `xg`, `corners`, `fouls`, `yellow_cards`, `red_cards`, `offsides`, `pass_accuracy`, `ppda`, `box_entries`.
- Tính bằng catalog Stats Tools, cùng đường với claim, nên số trên bảng khớp với số trong claim và talking point.
- UI gọi lại khi có `snapshot` (debounce 2 s).

### 3.5. Tra event (bằng chứng, lấp feed)

`GET /matches/{id}/events?ids=a,b,c` (≤ 200 ID) hoặc `?last=N` (≤ 300) → `EventsPage {events, missing}`.

- **Chỉ trả event đã áp dụng** (`seq ≤ latest_seq`). ID chưa tới hoặc không tồn tại nằm trong `missing`, không báo lỗi. Có property test: không bao giờ trả event tương lai.
- `?last=N` dùng để lấp feed sự kiện ngay sau khi kết nối (vì `hello` không chứa event). Bỏ `player_physical` như luồng WS.

### 3.6. Danh sách cho trang chọn trận

- `GET /scenarios` → `[ScenarioInfo]`, mỗi mục gồm:
  - `key`, `title_vi`, `title_en`, `home` / `away` (`TeamBrief`), `default_seed: 42`.
  - `example_claims: {vi: [...], en: [...]}`: 3–4 câu mỗi ngôn ngữ. `dominant_loser` lấy từ bộ eval Ngày 2 (`evals/claims_dominant_loser_42.json`). Các kịch bản khác viết tay trong catalog, có cả câu đúng lẫn câu sai.
- `GET /matches` → `[MatchInfo]` của các phiên đang có.
- `TeamBrief` thêm `primary_color`, `secondary_color` (lấy từ roster) để scoreboard dùng màu đội.

### 3.7. Phục vụ UI tĩnh

- `Settings.ui_dir` (env `UI_DIR`) và `pundit serve --ui PATH`.
  - Có thư mục: mount `StaticFiles(directory, html=True)` tại `"/"` **sau cùng**, nên mọi đường API và `/docs` được khớp trước.
  - Không có: app y như Ngày 3.
- Header: HTML `Cache-Control: no-cache` (OBS luôn lấy bản mới); `/_next/static/*` `public, max-age=31536000, immutable`.
- Test:
  - Không trang nào trùng đường API (`/matches`, `/claims`, `/healthz`, `/scenarios`, `/ws`).
  - `GET /studio/` trả HTML; `GET /matches` vẫn trả JSON.

### 3.8. Hợp đồng và schema

- `WsType` thêm `on_air`. Loại message mới chỉ thêm; client cũ bỏ qua (quy ước Ngày 3).
- `pundit schema` xuất thêm:
  - `match_info`, `match_view` (kèm `MatchSnapshot`), `studio_item_detail`, `trace_span`, `on_air`, `timeline`, `stats_panel`, `events_page`, `scenario_info`, `health`.
  - **`ws_payloads.schema.json`**: schema payload cho từng loại message WS (map `type → $ref`).
- Frontend sinh type từ các file này.
- Contract test (pytest): mọi schema xuất ra khớp model; ví dụ thật ghi từ một phiên hợp lệ với schema.

### 3.9. Test backend mới (~60 test)

| Nhóm | Ca chính |
|---|---|
| Replay control | Pause giữ job studio; resume không mất / lặp event; đổi tốc độ khi đang chạy; `n` liên tục; `status` có `speed`; restart phát `restarted: true` **sau** khi phiên mới đã đăng ký; 404 / 422 |
| On-air | Đủ bảng chuyển trạng thái; `revision` tăng; Take ID lạ → 404; `hello` có `on_air`; hai client nhận cùng message |
| Panels | Timeline khớp `momentum_series`; stats khớp catalog; không lộ event tương lai (property); cache đúng theo `as_of_seq` |
| Events | `ids` có ID tương lai → nằm trong `missing`; `last` bỏ `player_physical`; giới hạn 200 / 300 |
| Static UI | Mount sau API; header cache đúng; không mount khi thiếu `UI_DIR` |
| Tương thích | `STUDIO_ENABLED=false`: hình dạng Ngày 2 không đổi; toàn bộ 1764 test cũ xanh |

---

## 4. Nền tảng frontend

### 4.1. Cấu hình Next

```ts
// next.config.ts
const config: NextConfig = {
  output: "export",
  trailingSlash: true,          // /studio/ → out/studio/index.html
  images: { unoptimized: true },
  reactStrictMode: true,
};
```

- Mọi trang là client component (`"use client"`), bọc trong `<Suspense>` vì đọc `useSearchParams` (bắt buộc với static export).
- `NEXT_PUBLIC_API_BASE`: rỗng ở bản build (cùng origin); `http://localhost:8000` khi `npm run dev`. URL WS suy ra từ API base (`http` → `ws`, `https` → `wss`; rỗng → host hiện tại).
- Script npm: `dev`, `build`, `gen:types`, `lint`, `typecheck` (`tsc --noEmit`), `format:check`, `test` (Vitest), `e2e`, `e2e:mock`, và `check` (gen:types + lint + typecheck + format:check).

### 4.2. Type sinh từ schema

- `npm run gen:types` đọc `../packages/schemas/*.schema.json` và sinh `src/lib/contracts/*.ts` bằng `json-schema-to-typescript`. Đầu file ghi "DO NOT EDIT". File sinh ra được commit.
- `src/lib/ws-types.ts` (viết tay, nhỏ):
  - `WsPayloadMap = { hello: HelloPayload; event: MatchEvent; …; on_air: OnAirState }`.
  - `WsMessage<T extends WsType> = { v: 1; type: T; n: number; match_id: string; payload: WsPayloadMap[T] }`.
  - Test type (`expectTypeOf`) bảo đảm map phủ đủ mọi giá trị của `WsType` đã sinh.
- CI chạy `npm run gen:types && git diff --exit-code src/lib/contracts`: lệch là fail.

### 4.3. REST client (`lib/api.ts`)

- `fetch` có timeout: mặc định 8 s; `POST /claims/verify` 20 s vì claim có ngân sách LLM.
- Trả `Result<T> = {ok: true, data} | {ok: false, error: ApiError}`, với `ApiError {status, code, message}` lấy từ `detail` của FastAPI. Không `throw` lên render.
- Lỗi được đổi sang câu dễ hiểu theo ngôn ngữ UI:
  - `404`: trận hoặc item không tồn tại.
  - `409`: recap chưa có.
  - `422`: dữ liệu nhập sai (hiện `detail`).
  - Mạng / timeout: "Không kết nối được máy chủ".

### 4.4. WebSocket client (`lib/socket.ts`)

Máy trạng thái: `idle → connecting → open → (reconnecting → connecting …) | waiting | fatal | closed`.

| Tình huống | Xử lý |
|---|---|
| Mở kết nối | URL `ws(s)://host/ws/match/{id}?{profileQuery}`; message đầu tiên phải là `hello` |
| `hello` / `resync` | Thay toàn bộ state; `expectedN = n + 1` |
| `n == expectedN` | Áp dụng, rồi `expectedN++` |
| `n < expectedN` | Trùng: bỏ, đếm `duplicates` |
| `n > expectedN` | Hở: đóng và kết nối lại ngay (nhận `hello` mới), đếm `gaps` |
| `type` lạ | Bỏ qua (forward compatible) |
| JSON hỏng | Bỏ, đếm `bad_frames` |
| Đóng `4404` | Studio / Fan: `fatal` "Trận chưa được tạo", kèm nút **Tạo trận này** (`POST /matches` với kịch bản và seed tách từ ID). Overlay: `waiting`, thử lại mỗi 5 s, vì trong OBS overlay thường được mở trước khi Studio tạo phiên |
| Đóng `4422` | `fatal`: hồ sơ sai → xoá hồ sơ đã lưu, mở lại màn chọn hồ sơ |
| Đóng `4429` | Thử lại sau 10 s, hiện "Trận đang quá tải người xem" |
| Đóng khác / lỗi mạng | `reconnecting`: chờ 0,5 → 1 → 2 → 4 → 8 s (± 20 % jitter, trần 8 s), thử mãi |
| `status.restarted` | Kết nối lại sau 300 ms |
| Không có message 45 s khi trận đang `running` | Coi là treo, kết nối lại |
| React StrictMode (effect chạy hai lần ở dev) | Mỗi socket mang một `generation`; handler của socket cũ bị bỏ qua; cleanup đóng socket |

Socket giữ số đếm chẩn đoán (`gaps`, `duplicates`, `reconnects`, `bad_frames`, độ trễ nhận → hiển thị) cho panel debug (`?debug=1`) và để test kiểm tra.

### 4.5. Store và reducer (`store/match-store.ts`)

```ts
type MatchState = {
  match: MatchInfo | null; snapshot: MatchSnapshot | null; status: SessionStatus;
  speed: number; degraded: string[]; profile: Profile | null; onAir: OnAirState;
  events: RingBuffer<MatchEvent> /* 300 */; moments: Moment[] /* ≤ 200 */;
  claims: Ordered<ClaimResult> /* ≤ 50 */; talkingPoints: Ordered<TalkingPoint> /* ≤ 100 */;
  lowerThirds: Ordered<LowerThirdView> /* ≤ 30 */; commentary: Ordered<CommentaryView> /* ≤ 200 */;
  recaps: { H1?: RecapView; FULL?: RecapView };
  pending: { talkingPoints: Record<string, TalkingPointStatus>; onAir: OnAirAction | null };
  lastN: number;
};
function reduce(state: MatchState, message: AnyWsMessage): MatchState; // hàm thuần
```

| Message | Hiệu lực trên state |
|---|---|
| `hello`, `resync` | State mới từ payload; giữ `pending` chưa được server xác nhận |
| `event` | Thêm vào ring buffer (khử trùng theo `id`) |
| `snapshot` | Thay `snapshot` |
| `moment` | Thêm (khử trùng) |
| `claim` | Thêm hoặc thay theo `claim_id` |
| `status` | `status`, `speed`, `degraded`; `restarted` → báo socket kết nối lại |
| `talking_point` | Thêm hoặc thay theo `id` |
| `talking_point_status` | Đặt `status`; xoá `pending` của item đó |
| `lower_third` | Thêm; áp `supersedes` (ẩn món bị thay) |
| `commentary` | Thêm hoặc thay theo `id` |
| `recap` | Đặt theo `scope`, chỉ nhận `revision` lớn hơn |
| `on_air` | Chỉ nhận khi `revision` lớn hơn |

- **Gom theo khung hình:** socket đẩy message vào hàng đợi; `frame-batcher` áp dụng tất cả trong một `requestAnimationFrame` và gọi `setState` một lần. Ở replay 1000x vẫn chỉ render tối đa 60 lần / giây. Test gọi `flush()` để áp dụng ngay.
- Mỗi kết nối có một store riêng (`createMatchStore()`); màn so sánh có hai store.

### 4.6. Định dạng phút và toạ độ

- **Phút:**
  - Scoreboard dùng `snapshot.clock_display`. Moment, bình luận, talking point và lower-third đã có `display_minute` / `match_clock` từ server.
  - Riêng dòng event trong feed cần định dạng từ `period` + `clock_ms`. `lib/clock.ts` là bản sao 3 dòng của `domain.matchtime.format_minute` (`45+2'`, `90+4'`).
  - Có **test vàng**: với mọi moment trong fixture, `formatMinute(event.period, event.clock_ms)` của event gây ra moment phải bằng đúng `display_minute` server gửi.
- **Toạ độ** (Day 1 §3.1):
  - `x`, `y` tính bằng mét trên sân 105 × 68, theo khung tấn công của đội thực hiện event (đội đó luôn tấn công về `x = 105`).
  - `lib/pitch.ts` vẽ event của đội nhà nguyên toạ độ và lật event của đội khách (`105 − x`, `68 − y`), để hai đội tấn công ngược chiều trên cùng một sơ đồ.
  - Đây là phép chiếu hình học, không phải số liệu.

### 4.7. Hồ sơ người xem phía client (`lib/profile.ts`)

- `Profile {mode, language, team?, player?, beginner, metric?}` ↔ query `mode`, `lang`, `club`, `player`, `beginner=1`, `metric` (đúng tên mà `parse_profile` đọc).
- Lưu ở `localStorage["pundit.profile"]`, bọc `try/catch`: trang vẫn chạy khi storage bị chặn.
- Trước khi kết nối, kiểm tra `team` / `player` có thuộc trận này không (từ `MatchInfo` + đội hình). Không thuộc thì bỏ trường đó, để tránh vòng lặp `4422`.

### 4.8. Ngôn ngữ giao diện (đã chốt: song ngữ, có nút chuyển)

- `i18n/en.ts` là bản chuẩn; `vi.ts` có kiểu `Dict = typeof en`, nên thiếu khoá là lỗi biên dịch. `useT()` trả về hàm `t(key, vars)`.
- Mặc định: Studio, overlay, so sánh và trang chọn trận dùng `en`; Fan dùng `vi`. Thứ tự ưu tiên: `?ui=` > `localStorage["pundit.ui-lang"]` > mặc định của trang.
- Nhãn verdict, mã lý do (`reason_code`), tên loại event / moment / chỉ số đều có hai bản trong từ điển.
- Chữ do server sinh (bình luận, talking point, lower-third, recap) không bao giờ được dịch ở client.
- `SyntheticBadge`: "SYNTHETIC DATA" / "DỮ LIỆU TỔNG HỢP" theo ngôn ngữ UI, luôn hiện ở góc trên, không ẩn được.

### 4.9. Giao diện, màu, font

- **Studio:** nền tối kiểu phòng điều khiển, dễ đọc khi quay màn hình. **Fan:** theo `prefers-color-scheme`, tương phản cao. **Overlay:** chỉ có lower-third.
- Token CSS: `--bg`, `--panel`, `--text`, `--muted`, `--accent`, `--true`, `--partly`, `--false`, `--unknown`.
- Màu đội lấy từ `TeamBadge` / `TeamBrief`. Chữ đặt trên nền màu đội chọn đen hoặc trắng theo độ sáng, đạt WCAG AA (≥ 4.5:1).
- Verdict: `TRUE` xanh lá ✓, `PARTLY_TRUE` hổ phách ◐, `FALSE` đỏ ✗, `INSUFFICIENT_DATA` xám ?. Luôn có **chữ + biểu tượng**, không chỉ dựa vào màu.
- Font Be Vietnam Pro. Số dùng `font-variant-numeric: tabular-nums` để đồng hồ và tỷ số không nhảy chiều rộng.

### 4.10. Khả năng truy cập và phím tắt

- Feed bình luận và verdict mới có `aria-live="polite"`. Drawer giữ focus bên trong; `Esc` để đóng. Mọi nút có nhãn.
- Phím tắt ở Studio (tắt khi đang gõ):
  - `/`: vào ô claim. `Space`: play / pause.
  - `A` / `P` / `D`: duyệt / ghim / bỏ talking point đang chọn.
  - `T`: Take lower-third đang chọn. `C`: Clear.
- `prefers-reduced-motion`: animation chỉ còn mờ dần.

---

## 5. Studio Dashboard (`/studio/?match=ID`)

### 5.1. Bố cục (thiết kế cho 1920×1080, dùng được từ 1280 px)

```
┌ TopBar: [HAR 1–2 KES] 63' ● LIVE 20x │ ▶/⏸ 1x 5x 20x │ ↺ từ phút 0·45·60·75 │ Overlay↗ Fan↗ So sánh↗ │ EN/VI │ SYNTHETIC DATA ┐
├──────────── trái (25 %) ────────────┬──────────── giữa (45 %) ─────────────┬──────────── phải (30 %) ────────────┤
│ EventFeed (mới nhất trên cùng)       │ ClaimBox + VerdictCard               │ LowerThirdMonitor                   │
│ [Tất cả | Sự kiện chính]             │ Lịch sử claim (cả của người khác)    │  on-air + hàng đợi                  │
│                                      │ TalkingPointQueue                    │  AUTO / MANUAL / HOLD · Take · Clear│
│                                      │  [Mới | Ghim | Đã duyệt | Đã bỏ]     │ StatsPanel (P1)                     │
│                                      │ CommentaryPreview (analyst)          │ MomentumTimeline (P1)               │
└──────────────────────────────────────┴──────────────────────────────────────┴─────────────────────────────────────┘
                              Drawer bên phải (60 % chiều rộng): Why | Evidence | Agent Trace
```

Studio kết nối WS với hồ sơ `mode=analyst&lang=<ngôn ngữ UI>`. Talking point không bị cá nhân hoá, có sẵn cả EN và VI.

### 5.2. TopBar và điều khiển replay (P0)

- **Scoreboard:**
  - Tên ngắn và màu của hai đội; tỷ số và `clock_display` lấy từ `snapshot`.
  - Giữa hai snapshot, phút lấy theo event mới nhất (`lib/clock.ts`). Không tự đếm giờ ở client.
  - Chip trạng thái: `created` / `running` / `paused` / `finished` / `stopped` / `error`.
- **Nút điều khiển:**
  - Play (`/start` hoặc `/resume`), Pause (`/pause`), tốc độ 1x / 5x / 20x (`/speed`).
  - "Chạy lại từ phút" 0 / 45 / 60 / 75 (`POST /matches`, `restart: true`).
  - Nút bị khoá trong lúc chờ phản hồi. Trạng thái thật chỉ đổi khi có message `status` (U2).
- **Chip LLM** lấy từ `/healthz`: provider (`mock` / `ollama` / `foundry`), model, `degraded`.

### 5.3. EventFeed (P0)

- Mỗi event một dòng: phút, biểu tượng theo `type`, đội (màu), cầu thủ, kết quả.
- Bộ lọc "Sự kiện chính": `goal`, `shot`, `card`, `substitution`, `var_check`, `offside`, phạt góc, `period_start` / `period_end`.
- Khi vừa kết nối: lấp 100 event gần nhất bằng `GET /events?last=100`, rồi nối luồng live (khử trùng theo `id`).
- Bấm vào một dòng: popover vị trí trên sơ đồ sân nhỏ (P1).

### 5.4. ClaimBox và VerdictCard (P0)

- **Ô nhập:** tối đa 300 ký tự (giới hạn của backend); chip câu mẫu của kịch bản (từ `/scenarios`); Enter để gửi; trong lúc chờ hiện spinner và khoá nút.
- **Gửi:** `POST /claims/verify {match_id, text, language: "auto"}`.
- **VerdictCard:** kết quả trả về, và cả message `claim` của người khác, đều hiện thành một thẻ gồm:
  - Nhãn verdict có màu + biểu tượng; câu claim.
  - "Tính tới phút X" (`as_of_display`).
  - Headline facts (`label: display`) và lời giải thích.
  - Chip nguồn (`explanation_source`, `analyst_source`); chip `degraded` nếu có.
  - Nút **Why / Evidence / Trace** để mở drawer.
- **Lỗi:** `422` hiện `detail` ngay dưới ô nhập. Lỗi mạng / timeout hiện toast, giữ nguyên chữ đã gõ.

### 5.5. TalkingPointQueue (P0)

- Tab Mới / Ghim / Đã duyệt / Đã bỏ, kèm số đếm. Talking point đã ghim luôn nằm đầu tab Mới.
- **Thẻ:** phút, loại trigger, importance; 2–3 dòng EN và VI (nút chọn hiện EN / VI / cả hai); chip fact (`label: display`); chip nguồn của từng ngôn ngữ.
- **Duyệt / Ghim / Bỏ** gọi `POST /talking-points/{id}/status`.
  - Thẻ hiện "đang gửi" tới khi nhận `talking_point_status`.
  - REST lỗi: trả lại trạng thái cũ và hiện toast.
  - Hai biên tập viên bấm cùng lúc: message cuối của server thắng, cả hai tab hiện giống nhau.
- Nút **Trace** mở drawer cho item này (`GET /matches/{id}/studio/{item_id}`).

### 5.6. LowerThirdMonitor (P0)

- **Khung "On air":** dùng đúng component của overlay, thu nhỏ 50 %, chạy cùng bộ lập lịch (§6.3), nên thấy đúng món đang lên sóng.
- **Điều khiển:**
  - Chip chế độ `AUTO` / `MANUAL` / `HOLD`.
  - Hàng đợi 10 lower-third gần nhất, mỗi món có nút **Take**.
  - Nút chung **Clear**, **Hold / Release**, **Auto**. Các nút gọi `POST /on-air`; giao diện chỉ đổi khi nhận `on_air`.
- **Ngôn ngữ xem trước** EN / VI: khi khác ngôn ngữ UI thì mở thêm một kết nối WS `lang` tương ứng, chỉ cho khung này.
- Link **Mở overlay cho OBS** chứa sẵn `?match=…&lang=…`.

### 5.7. CommentaryPreview (P0)

Dòng bình luận analyst theo ngôn ngữ UI, mới nhất trên cùng, có chip `source` (llm / template) và nút mở drawer. Biên tập viên thấy người xem đang được nghe gì.

### 5.8. StatsPanel (P1) và MomentumTimeline (P1)

- **StatsPanel:**
  - Bảng hai cột (đội nhà | đội khách) từ `GET /stats?period=`, tab FULL / H1 / H2.
  - Thanh so sánh vẽ theo `value`, chữ là `display`. Gọi lại khi có `snapshot` (debounce 2 s).
- **MomentumTimeline:**
  - Biểu đồ vùng SVG: đội nhà phía trên, đội khách phía dưới; trục phút 0–90+.
  - Marker cho bàn thắng / VAR huỷ / thẻ đỏ / hết hiệp. Rê chuột hiện `display` + phút.
  - Gọi lại `GET /timeline` khi có `snapshot` hoặc `moment` (debounce 2 s).

### 5.9. Drawer Why / Evidence / Agent Trace (P0; sơ đồ sân P1)

Mở cho claim (`GET /claims/{id}`, có trace) hoặc studio item (`GET /matches/{id}/studio/{item_id}`).

| Tab | Claim | Studio item (talking point, bình luận, lower-third, recap) |
|---|---|---|
| **Why** | Verdict + mã lý do (giải thích bằng ngôn ngữ UI), lời giải thích, bảng assertion: đoạn claim, chỉ số, cửa sổ, giá trị hai bên (`display`), kết quả | Loại trigger, phút, câu chữ đã đăng, nguồn, kết quả Guardrail, cờ `degraded` (ví dụ "LLM quá ngân sách → dùng template") |
| **Evidence** | Fact (`label`, `display`, số event bằng chứng); danh sách event bằng chứng (`GET /events?ids=`); **sơ đồ sân** (P1) | Như claim, theo `facts` + `evidence_event_ids` của item |
| **Agent Trace** | Gantt các span: mỗi agent một hàng, thanh theo `start_ms` / `duration_ms`, màu theo `kind`, viền nổi bật khi `status` là `retry` / `fallback` / `rejected`; bấm span để xem `input` / `output` (JSON thu gọn) | Như claim. Với studio thấy rõ ba nhánh chạy song song (TalkingPointWriter ‖ GraphicsDesigner ‖ Commentator) giữa StudioIntake / FactPackBuilder và StudioPublisher |

- **Tóm tắt trên Gantt:** tổng thời gian, số lần gọi LLM, số bản nháp bị Guardrail chặn, có fallback hay không. Đây là bằng chứng trực quan cho hạng mục Best Multi-Agent System: vai trò riêng, handoff, phục hồi khi lỗi.
- **Sơ đồ sân:**
  - SVG 105 × 68 theo §4.6. Mỗi event là một chấm màu đội, hình theo loại (sút, chuyền, tắc bóng…).
  - Rê chuột hiện phút, cầu thủ, loại. Event trong `missing` (chưa tới hoặc không tồn tại) ghi rõ là không có.

---

## 6. Overlay (`/overlay/?match=ID&lang=en`)

### 6.1. Yêu cầu

- **Nền trong suốt:** `html, body { background: transparent }`, không thanh cuộn, không chrome nào khác.
- **Khung hình:** sân khấu 1920×1080 co theo khung nhìn (giữ tỉ lệ); lower-third ở góc dưới trái, trong vùng an toàn 5 %.
- **Nhãn:** mỗi lower-third có tag nhỏ **SYNTHETIC DATA** (U5). Trang không có tương tác.
- **Query:**
  - `match`: bắt buộc.
  - `lang`: `en` | `vi`, mặc định `en`.
  - `debug=1`: nền ô cờ để kiểm tra độ trong suốt, khung vùng an toàn, bảng JSON đang lên sóng.
- **Bundle riêng, nhẹ:** chỉ gồm socket, store tối giản, scheduler và component lower-third.

### 6.2. Bốn template

| `template` | Bố cục |
|---|---|
| `score_update` | Dải màu hai đội, tên ngắn, tỷ số lớn, `subtitle` (ví dụ người ghi bàn, phút) |
| `team_compare` | Tiêu đề; 1–4 hàng chỉ số `label · home · away`, thanh tô theo `highlight` |
| `player_card` | Tên cầu thủ, đội (màu), 1–4 chỉ số `label · value` |
| `momentum` | Tiêu đề, thanh momentum hai màu, `subtitle` |

Độ dài chữ đã bị chặn ở server (title ≤ 32, subtitle ≤ 48 ký tự, tối đa 4 chỉ số). Component vẫn cắt bằng ellipsis để chắc chắn không tràn khung.

### 6.3. Bộ lập lịch (`lib/overlay-scheduler.ts`, hàm thuần)

`schedule(state, input, now) → {current, queue, nextWakeupMs}`. Input là một trong: lower-third mới, `on_air` mới, `speed` mới, hoặc nhịp thời gian.

| Chế độ | Luật |
|---|---|
| `auto` | **Thứ tự:** hàng đợi sắp theo `priority` giảm dần rồi `show_at_match_ms` tăng dần; tối đa 5 món (đầy thì bỏ món priority thấp nhất, cũ nhất). Rảnh thì hiện món đầu. **Thời gian hiện:** `clamp(duration_ms / speed, 4 s, duration_ms)`. **Chen ngang:** món có priority cao hơn thay món đang hiện sau khi món này đã lên ít nhất 2 s. **`supersedes`:** gỡ món bị thay khỏi hàng đợi; nếu món đó đang hiện thì thay ngay |
| `manual` | Chỉ hiện `item_id` được Take, không hết giờ, tới khi Clear / Take món khác / Auto. Item chưa có trong store (overlay vào trễ) thì lấy qua `GET /lower-thirds?lang=` |
| `hold` | Giữ món đang hiện, không hết giờ. Món mới vẫn vào hàng đợi và hiện sau khi Release |
| Clear | Chạy animation ra. Ở manual thì màn trống; ở auto thì món kế tiếp hiện sau 1 s |

Hai overlay mở cùng lúc nhận cùng một luồng message nên chạy cùng thứ tự; chỉ lệch thời điểm theo lúc kết nối.

### 6.4. Animation

- **Vào:** trượt lên 24 px + hiện dần, 450 ms, ease-out. **Ra:** mờ + trượt xuống, 300 ms.
- Thuần CSS (`transform`, `opacity`), không gây layout lại.
- `prefers-reduced-motion`: chỉ mờ dần.

### 6.5. Dữ liệu máy đọc được, có mốc thời gian (Rules: "timed and machine-readable")

- Payload `LowerThirdView` đã có từ Ngày 3: `match_clock`, `show_at_match_ms`, `duration_ms`, `priority`, `supersedes`, `synthetic`, `disclaimer`. `GET /matches/{id}/lower-thirds` trả toàn bộ dưới dạng JSON.
- Overlay ghi món đang hiện vào hai chỗ:
  - Thẻ `<script id="pundit-on-air" type="application/json">`.
  - Biến `window.punditOverlay = {current, queue, on_air, shown_at}`.
- Công cụ khác (vMix, CasparCG, test) đọc được trực tiếp mà không cần phân tích giao diện.

### 6.6. Đưa vào OBS (P1, thủ công)

OBS chưa cài trên máy dev. Các bước:

1. Cài OBS Studio 30 trở lên.
2. Thêm **Browser Source**: URL `http://localhost:8000/overlay/?match=dominant_loser-42&lang=en`, kích thước 1920×1080.
3. Bật "Refresh browser when scene becomes active"; tắt "Shutdown source when not visible".
4. Kiểm tra trên một video nền bất kỳ: nền trong suốt, animation mượt, chữ tiếng Việt đủ dấu (`lang=vi`).

---

## 7. Fan VN Player (`/fan/?match=ID`)

### 7.1. Luồng

1. **Vào trang:** từ trang chọn trận, hoặc trực tiếp với `?match`. Chưa có hồ sơ (hoặc bấm "Đổi hồ sơ") thì mở **ProfileSetup**.
2. **ProfileSetup** (P0):
   - Hai thẻ lớn *Chuyên sâu (Analyst)* và *Dễ hiểu (Casual)*, mỗi thẻ có một câu ví dụ thật lấy từ bình luận gần nhất của trận.
   - Ngôn ngữ VI / EN; CLB yêu thích (hai đội của trận, hoặc "Trung lập"); công tắc **Người mới xem**.
   - Phần nâng cao: theo dõi một cầu thủ (P1, danh sách theo đội), một chỉ số (P2).
3. **Màn chính:**
   - Header: tỷ số, phút, trạng thái; công tắc *Người mới xem* luôn hiện.
   - Tab **Bình luận** | **Cầu thủ** (khi bật Player focus) | **Recap**.
   - Trận chưa bắt đầu (`created`) thì hiện "Đang chờ trận bắt đầu".

### 7.2. Bình luận (P0)

- Mới nhất trên cùng. Người dùng đang cuộn xuống đọc lại thì không tự nhảy, chỉ hiện nút "N bình luận mới".
- `CommentaryCard` hiển thị:
  - Phút và `text`; `framing` (câu mở đầu theo CLB) in nghiêng phía trên.
  - `excitement` 0–3 đổi cỡ chữ và độ nhấn (bàn thắng nổi bật nhất).
  - `emphasis` = `own_team` / `opponent` / `focus_player` tô viền màu đội. `relevance: low` thì mờ và thu gọn.
  - `facts` (chỉ ở analyst) thành chip `label: display`.
- **Giải thích luật** (`explanation`, khi bật Người mới xem): hộp "Luật: {title}" + `text`. `depth: full` hiện đầy đủ; `short` hiện một dòng, có nút mở rộng.
- **Đổi hồ sơ** (kể cả bật / tắt Người mới xem) là mở kết nối WS mới với query mới. `hello` trả 30 bình luận gần nhất đã render theo hồ sơ mới, nên feed thay ngay và không lẫn câu của hồ sơ cũ.

### 7.3. Player focus (P1)

Tab **Cầu thủ** gồm:
- Thẻ cầu thủ: tên, đội.
- `player_tracker` mới nhất (`label: display`): chạm bóng, đường chuyền, quãng chạy, tốc độ…
- Danh sách bình luận có `emphasis = focus_player`.

### 7.4. Recap (P0, văn bản)

- Nút H1 / FULL. Chưa có (`409`) thì hiện "Recap có sau giờ nghỉ" / "sau trận".
- Message `recap` tới thì tự hiện. `revision 2` (bản LLM) thay `revision 1`, kèm nhãn "đã cập nhật".

### 7.5. Chỗ cho giọng đọc (Ngày 5)

- `AudioQueue` (chỉ giao diện; Ngày 4 không phát gì): `enqueue(view)` khi `view.speak`, ưu tiên theo `excitement`, hàng đợi dài quá 3 thì bỏ món cũ.
- Ngày 5 thêm `audio_url` vào `CommentaryView` (hợp đồng §21 của Ngày 3) và cài phần phát.
- Ngày 4 không hiện nút âm thanh, để không hứa trước tính năng chưa có.

### 7.6. Responsive

Thiết kế cho điện thoại trước (360–430 px). Trên desktop chia hai cột: feed | recap / cầu thủ.

---

## 8. Màn so sánh (`/compare/?match=ID&left=analyst.en&right=casual.vi.beginner`)

- **Mục đích:** quay đoạn 20 s "Analyst và Casual xem cùng một moment" của video, và để giám khảo tự thấy yêu cầu Personalize của Rules.
- **Hai kết nối:** hai `MatchSocket` + hai store với hai hồ sơ. Mặc định trái là `analyst · en`, phải là `casual · vi · beginner`; mỗi cột đổi được.
- **Căn hàng:** cùng một item bình luận có cùng `id` ở cả hai kết nối (một `n`, hai payload; tính chất S-10 của Ngày 3). `lib/compare.ts` căn hàng theo `id`; hàng mới nhất ở trên và được tô nổi.
- **Chip khác biệt:** dưới mỗi cặp hiện số chỉ số được nêu, có giải thích luật hay không, độ dài câu. Chip chỉ để **hiển thị** khác biệt, không chấm điểm.

---

## 9. Trang chọn trận (`/`)

- **Kịch bản:** danh sách 4 kịch bản (`/scenarios`), tiêu đề theo ngôn ngữ UI, hai đội có màu, ô seed (mặc định 42), tốc độ mặc định 20x.
- **Nút cho mỗi kịch bản:**
  - **Studio:** `POST /matches {autostart: false}`, để người điều khiển tự bấm Play.
  - **Xem như fan:** `autostart: true`.
  - **Overlay**, **So sánh**.
  - `POST /matches` idempotent: phiên đang có thì vào chung.
- **Phiên đang có** (`GET /matches`): kịch bản, seed, trạng thái, phút.
- **Chip máy chủ** từ `/healthz`: LLM provider, model, `degraded`. Câu tuyên bố dữ liệu tổng hợp hiện rõ trên trang.

---

## 10. Bảng message → màn hình

| Message | Studio | Overlay | Fan | So sánh |
|---|---|---|---|---|
| `hello` / `resync` | toàn bộ | `lower_thirds`, `on_air`, `match` | `commentary`, `recaps`, `match`, `profile` | toàn bộ (mỗi cột) |
| `event` | EventFeed, scoreboard | — | header (phút) | header |
| `snapshot` | scoreboard; gọi lại stats / timeline | — | header | header |
| `moment` | EventFeed (nổi bật), timeline | — | — | — |
| `claim` | lịch sử claim | — | — | — |
| `status` | TopBar, nút điều khiển | `restarted` → kết nối lại | header | header |
| `talking_point`, `talking_point_status` | TalkingPointQueue | — | — | — |
| `lower_third` | LowerThirdMonitor | scheduler | — | — |
| `on_air` | LowerThirdMonitor | scheduler | — | — |
| `commentary` | CommentaryPreview | — | CommentaryFeed, PlayerTracker | hai cột |
| `recap` | badge "có recap" | — | RecapPanel | — |

---

## 11. Chiến lược test trong lúc xây

### 11.1. Các tầng

| Tầng | Công cụ | Thư mục | Nội dung | Ước lượng |
|---|---|---|---|---|
| Backend unit / API / WS | pytest | `backend/tests/api/`, `tests/property/` | §3.9 | ~60 |
| Backend contract | pytest + jsonschema | `backend/tests/contract/` | Schema mới khớp model; ví dụ thật hợp lệ | ~10 |
| Frontend unit | Vitest | `frontend/tests/unit/` | reducer, socket (với WebSocket giả), scheduler, compare, profile ↔ query, `formatMinute`, phép chiếu sân, i18n đủ khoá, api lỗi / timeout, chọn màu chữ trên nền đội | ~120 |
| Frontend property | Vitest + fast-check | `frontend/tests/unit/` | **Reducer:** message trùng không đổi state; mọi chỗ hở `n` dẫn tới kết nối lại; giới hạn danh sách luôn giữ; `type` lạ không đổi state. **Scheduler:** không bao giờ hiện hai món cùng lúc; `manual` không hết giờ; `supersedes` luôn thắng | ~15 |
| Component | Vitest + Testing Library (jsdom) | `frontend/tests/component/` | VerdictCard đủ 4 verdict (màu + nhãn + biểu tượng); TalkingPointQueue đủ thao tác, rollback khi lỗi; Drawer 3 tab; CommentaryCard theo excitement / emphasis / explanation; LowerThird 4 template; ProfileSetup; SyntheticBadge | ~70 |
| Contract (FE) | Vitest + ajv | `frontend/tests/contract/` | Mọi message trong fixture hợp lệ với schema; type sinh ra khớp schema hiện tại; test vàng `formatMinute` | ~10 |
| E2E | Playwright | `frontend/tests/e2e/` | §12 F5: backend thật (mock LLM) phục vụ bản build | ~25 |
| E2E nhanh, không cần backend | Playwright `page.routeWebSocket` + `page.route` | `frontend/tests/e2e-mock/` | Phát lại fixture; bố cục, overlay, lỗi mạng mô phỏng, mã đóng 4404 / 4422 / 4429 | ~15 |
| A11y / hình ảnh | `@axe-core/playwright`, screenshot | trong E2E | 0 vi phạm serious / critical; ảnh tham chiếu 1920×1080 và 390×844 | ~10 |

### 11.2. Fixture: luồng WS thật được ghi lại

- **Fixture A** (`backend/scripts/record_ws.py`):
  - Lệnh: `--scenario dominant_loser --seed 42 --profiles analyst.en,casual.vi.beginner,casual.vi.club=HAR --out ../frontend/tests/fixtures/`.
  - Script dựng app với `SteppingClock` (chạy nhanh nhất có thể, tất định), mở một client WS cho mỗi hồ sơ và ghi mọi message thành JSONL.
  - Ghi thêm `hello` của một client vào giữa trận, và một lần `restart`.
- **Fixture B** (câu dài thật): chuyển `ollama3.jsonl` của P6 Ngày 3 thành message `commentary` / `talking_point` / `lower_third` / `recap`.
- Fixture được commit; chạy lại script khi hợp đồng đổi. Contract test (ajv) chặn fixture đã lỗi thời.

### 11.3. Ca bắt buộc theo module

| Module | Ca |
|---|---|
| `socket.ts` | hello → áp dụng; hở `n` → kết nối lại + hello mới; trùng → bỏ; 4404 → fatal (Studio / Fan) hoặc chờ 5 s (overlay); 4422 → fatal, không thử lại; 4429 → chờ 10 s; backoff có trần 8 s; `restarted` → kết nối lại sau 300 ms; watchdog 45 s; StrictMode mở / đóng hai lần không để lại hai kết nối sống |
| `match-store.ts` | Từng loại message trong bảng §4.5; giới hạn danh sách; `supersedes`; `revision` của recap và on-air; hello giữ pending chưa được xác nhận |
| `overlay-scheduler.ts` | Đủ bảng §6.3; thời gian hiện ở 1x / 20x / 1000x; chen ngang; hàng đợi đầy; manual với item vào trễ |
| `compare.ts` | Căn theo `id`; một bên thiếu item; thứ tự giữ đúng theo `n` |
| `profile.ts` | Khứ hồi profile ↔ query; club / player không thuộc trận bị bỏ; storage ném lỗi vẫn chạy |
| `api.ts` | Timeout; 404 / 409 / 422 / 500 thành `ApiError` đúng; không throw |
| `clock.ts`, `pitch.ts` | Test vàng với `display_minute` của server; lật toạ độ đội khách; điểm ở biên sân |
| `i18n` | `vi` đủ khoá (kiểm bằng biên dịch); không có chuỗi rỗng; đủ nhãn verdict và reason code |
| Trang | Mọi trang render được khi: chưa có `match`, trận không tồn tại, backend tắt, đang `paused`, đã `finished` |

### 11.4. Giữ an toàn cho Ngày 1–3

- Backend: toàn bộ `pytest -m "not live"` (1764 test hiện có) xanh sau mỗi gate; test hình dạng Ngày 2 với `STUDIO_ENABLED=false`; perf Ngày 2–3 xanh.
- Mọi thay đổi model là thêm trường có giá trị mặc định; test schema so khoá cũ (không mất khoá nào).
- Frontend không làm đổi hành vi server: chỉ gọi endpoint đã có hoặc endpoint mới của §3.

### 11.5. CI (`.github/workflows/ci.yml` thêm job `frontend`)

Thứ tự các bước:

1. `npm ci`.
2. `npm run gen:types`, rồi `git diff --exit-code`.
3. `npm run lint` → `npm run typecheck` → `npm run test` (Vitest) → `npm run build`.
4. E2E trên Linux: chạy nền `uv run pundit serve --ui ../frontend/out`, `npx playwright install --with-deps chromium`, rồi `npm run e2e`.

Job backend giữ nguyên.

---

## 12. Kế hoạch kiểm thử sau khi hoàn thành

| Pha | Việc | Đạt khi |
|---|---|---|
| **F1. Gate tĩnh** | Backend: `ruff check .`, `ruff format --check .`, `mypy`. Frontend: `npm run check` (gen:types không lệch, lint, typecheck, prettier) | Sạch |
| **F2. Hồi quy backend** | `pytest -m "not live"` (mặc định + slow + perf) | Toàn bộ xanh: 1764 test cũ + test mới §3.9 |
| **F3. Unit / component / property FE** | `npm run test -- --coverage` | Xanh; coverage `lib/` + `store/` ≥ 90 %, component ≥ 75 % |
| **F4. Hợp đồng** | Contract test hai phía; ghi lại fixture bằng `record_ws.py` rồi chạy lại F3 | Không lệch schema; mọi message thật hợp lệ |
| **F5. E2E trên backend thật** (mock LLM, bản build do FastAPI phục vụ) | Kịch bản E1–E12 bên dưới | 12/12 |
| **F6. A11y, hình ảnh, độ trong suốt** | axe trên 5 trang; screenshot 1920×1080 (Studio, Overlay `debug`, So sánh) và 390×844 (Fan); overlay: lấy mẫu pixel nền ngoài lower-third phải có alpha = 0 | 0 serious / critical; ảnh được duyệt bằng mắt một lần rồi làm tham chiếu |
| **F7. Hiệu năng** | Replay 1000x cả trận khi Studio + Fan + Overlay cùng mở; đo bằng Playwright trace, `PerformanceObserver` (long task) và `performance.memory` (Chromium); `scripts/bundle-size.mjs` | Không long task > 200 ms; độ trễ message → hiển thị p95 < 100 ms ở 20x; bộ nhớ tab tăng < 50 MB sau cả trận; JS tải lần đầu: overlay < 120 KB gzip, Studio < 300 KB gzip |
| **F8. Nghiệm thu thủ công** | Kịch bản M1–M8 bên dưới, quay màn hình ngắn | Đạt hết; lỗi tìm thấy đều có test hồi quy |
| **F9. Ghi kết quả** | Mục "As-built" của tài liệu này; tick `checklist.md`; ghi quyết định vào `claude.md`; commit local | Xong |

**E2E (F5).** Câu claim lấy từ bộ eval Ngày 2 (`evals/claims_dominant_loser_42.json`), nên đáp án đã có sẵn:

| # | Kịch bản | Kiểm tra |
|---|---|---|
| E1 | Trang chọn trận → Studio `dominant_loser-42` | Scoreboard, nút điều khiển, nhãn Synthetic data |
| E2 | Play 20x → event chạy; Pause → event dừng nhưng job studio vẫn ra; Resume; đổi sang 5x | `n` liên tục; feed không có event trùng |
| E3 | Chạy lại từ phút 85 ở tốc độ cao tới hết trận, gửi *"Đội nhà áp đảo hiệp 2"* (e01) | Verdict **TRUE** màu xanh, có `61%`, `15`–`1`, `2.24`–`0.24`; drawer Why / Evidence / Trace có span và event bằng chứng; sơ đồ sân có chấm |
| E4 | Gửi lần lượt e09 *"HAR kiểm soát bóng 70% trong hiệp 2"*, e15 *"HAR áp đảo hiệp 2 và thắng trận"*, e20 *"Trọng tài thiên vị Harbor"* | Lần lượt **FALSE** đỏ, **PARTLY_TRUE** hổ phách, **INSUFFICIENT_DATA** xám; mỗi thẻ có chữ + biểu tượng |
| E5 | Có talking point → Duyệt / Ghim / Bỏ ở tab A | Tab B (một context khác) thấy cùng trạng thái trong < 1 s |
| E6 | Overlay ở chế độ auto | Lower-third xuất hiện có animation và tự ẩn; JSON `#pundit-on-air` có `match_clock`, `show_at_match_ms`, `duration_ms` |
| E7 | Studio Take một lower-third cũ → Hold → Clear → Auto | Overlay làm đúng từng lệnh |
| E8 | Fan: ProfileSetup casual · vi · HAR · Người mới xem | Bình luận tiếng Việt; lần đầu có việt vị / phạt góc thì có hộp giải thích luật; có câu mở đầu theo CLB |
| E9 | Fan đổi sang analyst · en | Feed thay bằng câu tiếng Anh có số, không lẫn câu của hồ sơ cũ |
| E10 | So sánh: cùng moment ở hai cột | Hai câu khác nhau, cùng `id`, cùng phút |
| E11 | Dừng backend 5 s rồi bật lại | Banner "đang kết nối lại"; không lỗi console; bật lại thì bắt kịp trạng thái |
| E12 | Studio "chạy lại từ phút 45" khi overlay và fan đang mở | Hai trang kia tự kết nối lại và theo phiên mới |

**Nghiệm thu thủ công (F8):**

| # | Việc |
|---|---|
| M1 | Đi đủ luồng "Hoàn thành khi" của checklist: claim, verdict, talking point, lower-third, bình luận tiếng Việt |
| M2 | Đọc lại toàn bộ chữ giao diện VI và EN: đúng dấu, không lẫn ngôn ngữ, đúng thuật ngữ bóng đá |
| M3 | Overlay trong OBS (P1) trên một video nền: trong suốt, mượt, chữ rõ ở 1080p |
| M4 | Bố cục với fixture B (câu LLM dài): không tràn, không cắt số |
| M5 | Fan trên điện thoại thật (hoặc DevTools 390×844) |
| M6 | Diễn tập kịch bản video 1:50 (checklist Ngày 7) trên local: mỗi đoạn đều có màn hình phù hợp |
| M7 | Hai cửa sổ Studio cùng điều khiển, thao tác xen kẽ: hai bên luôn khớp |
| M8 | Tắt hẳn backend khi đang xem: mọi trang hiện thông báo dễ hiểu, không trang nào trắng |

---

## 13. Quy trình thực hiện (thứ tự + gate)

| Bước | Nội dung | Gate (phải xanh mới đi tiếp) | Commit |
|---|---|---|---|
| G0 | **Spike (≤ 1 giờ):** dựng Next 16 static export + Tailwind 4 + Be Vietnam Pro; build xong cho FastAPI mount; mở `/studio/?match=…` và nhận `hello` qua WS cùng origin; chạy Playwright với `channel: "msedge"` | Trang build chạy trong FastAPI; WS nhận `hello`; Playwright mở được trang | `chore(frontend): scaffold` |
| G1 | Backend §3.1–3.8 + test §3.9 + schema mới + `record_ws.py` + fixture | `pytest -m "not live"` xanh; ruff / mypy sạch | `feat(api): replay control, on-air, panels, events, scenarios, static UI` |
| G2 | Nền frontend: type sinh, `api`, `socket`, `store` + batcher, `profile`, `clock`, `pitch`, i18n, layout (SyntheticBadge, ConnectionBanner, ErrorBoundary, Toast), trang chọn trận | Unit + property + contract FE xanh | `feat(frontend): contracts, socket, store, i18n, landing` |
| G3 | Studio P0: TopBar, EventFeed, ClaimBox, TalkingPointQueue, LowerThirdMonitor, CommentaryPreview, Drawer (Why / Evidence / Gantt cơ bản) | Component test + E1–E5 | `feat(studio): dashboard` |
| G4 | Overlay P0: 4 template, scheduler, animation, JSON máy đọc | Unit scheduler + E6–E7 + kiểm tra trong suốt | `feat(overlay): lower-third page` |
| G5 | Fan P0: ProfileSetup, CommentaryFeed, Người mới xem, Recap | Component + E8–E9 | `feat(fan): player` |
| G6 | P1: StatsPanel, MomentumTimeline, PitchMap, TraceGantt đầy đủ, Player focus | Component + snapshot hình | `feat(studio): timeline, stats, pitch map` · `feat(fan): player focus` |
| G7 | Màn so sánh | E10 | `feat(compare): side-by-side profiles` |
| G8 | Kế hoạch F1–F9, sửa lỗi, job CI frontend, as-built | Đạt đủ §12 | `test: Day 4 E2E, a11y, perf` · `docs: Day 4 as-built` |

- Commit local theo từng bước, chưa push (giống Ngày 2–3).
- Nếu trễ, cắt theo thứ tự: **sơ đồ sân → bảng chỉ số → Player focus → màn so sánh**. Không cắt Studio P0, overlay P0 và Fan P0.

---

## 14. Bảng chống bug

| # | Bug thường gặp | Cách chặn |
|---|---|---|
| 1 | Hai kết nối WS sống cùng lúc do StrictMode chạy effect hai lần | `generation` trong socket; cleanup đóng socket; test mở / đóng hai lần |
| 2 | Handler giữ state cũ (stale closure) | Handler chỉ đẩy message vào hàng đợi; reducer thuần luôn đọc state mới nhất |
| 3 | Hiện trùng hoặc mất mục sau khi kết nối lại | Khử trùng theo `id`; `hello` thay toàn bộ; kiểm tra `n` |
| 4 | Trang trắng khi một panel lỗi | Error boundary theo panel; test cố tình ném lỗi trong component con |
| 5 | `useSearchParams` làm build static lỗi | Bọc trang trong `<Suspense>`; CI luôn chạy build |
| 6 | Đồng hồ trận sai khi pause hoặc ở 20x | Không tự đếm giờ; chỉ dùng `clock_display` và phút của event mới nhất |
| 7 | Số hiển thị lệch số của server | U1: không tính ở client; component test so `display` nguyên văn |
| 8 | Overlay có nền trắng trong OBS | `background: transparent` ở `html`, `body` và layout của trang overlay; test lấy mẫu pixel; theme không đặt màu nền cho trang overlay |
| 9 | OBS giữ bản cũ sau khi build lại | HTML `no-cache`; asset có hash trong tên |
| 10 | Vòng lặp kết nối 4422 do hồ sơ hỏng trong storage | `profile.ts` kiểm tra theo đội hình; nhận 4422 thì xoá hồ sơ và mở màn chọn |
| 11 | `localStorage` ném lỗi (chế độ riêng tư, bị chặn) | Bọc `try/catch`; trang vẫn chạy với giá trị mặc định |
| 12 | Thao tác REST xong mà UI không đổi, hoặc đổi rồi bị bật lại | Optimistic có `pending`; server thắng khi message tới; REST lỗi thì rollback + toast |
| 13 | Hai biên tập viên đè lệnh nhau | Lệnh cuối của server thắng; `revision` của on-air; mọi tab hiện đúng trạng thái server |
| 14 | Render quá nhiều khi replay nhanh | Gom theo khung hình; giới hạn danh sách; component `memo` theo `id` |
| 15 | Chữ tiếng Việt lỗi dấu hoặc font thiếu glyph | Be Vietnam Pro với subset `vietnamese` + `latin`; screenshot có chữ VI |
| 16 | Đường trang trùng đường API | Danh sách đường trang cố định; test backend §3.7 |
| 17 | Lộ event tương lai qua `/events` | Lọc `seq ≤ latest_seq`; property test |
| 18 | Restart làm overlay / fan "đứng hình" | Báo `restarted` sau khi phiên mới đã đăng ký; client kết nối lại; E12 |
| 19 | Hydration mismatch | Trang chỉ chạy ở client; không render giá trị phụ thuộc thời gian / storage lúc build |
| 20 | Lỗi múi giờ | Không hiển thị giờ thực; chỉ dùng phút trận do server gửi |
| 21 | Type lệch sau khi đổi backend | `gen:types` + `git diff --exit-code` trong CI; contract test trên fixture |
| 22 | Màu đội quá nhạt, chữ khó đọc | Chọn chữ đen / trắng theo độ sáng; axe kiểm tra tương phản |
| 23 | Định dạng phút ở client lệch với server | `clock.ts` có test vàng trên mọi moment của fixture |
| 24 | Overlay mở trước khi Studio tạo phiên thì dừng hẳn | Overlay coi 4404 là "đang chờ", thử lại mỗi 5 s |

---

## 15. Rủi ro và phương án

| Rủi ro | Khả năng | Phương án |
|---|---|---|
| Windows Application Control chặn binary trong `node_modules` hoặc trình duyệt của Playwright | Thấp: esbuild và SWC đã chạy được (22/10) | Playwright dùng Edge / Chrome có sẵn (`channel`); nếu SWC bị chặn thì Next tự chuyển sang bản wasm; ghi lại ở as-built |
| Next 16 static export có giới hạn chưa lường trước | Thấp | Spike G0 trước khi xây. Dự phòng: Vite + React Router, giữ nguyên `lib/`, `store/` và component |
| E2E chập chờn do thời gian | Trung bình | Backend chạy tốc độ cao; test chờ theo điều kiện (`expect.poll`), không `sleep` cố định; phần giao diện thuần dùng E2E fixture-driven |
| Thời gian (một ngày làm việc) | Trung bình | Gate theo §13; cắt P1 theo thứ tự đã định |
| Không tải được font Google lúc build (mất mạng) | Thấp | Khai báo font hệ thống làm fallback; build không fail |
| Nhiều giám khảo cùng điều khiển một phiên trên bản public | Trung bình (Ngày 5 trở đi) | Ngày 4: trang chọn trận cho phép nhập seed khác để có phiên riêng. Chính sách chốt ở Ngày 5 |
| OBS chưa cài | Chắc chắn | Kiểm tra overlay bằng `?debug=1` trên trình duyệt; OBS là bước thủ công M3 |

---

## 16. Hợp đồng cho Ngày 5+

| Hợp đồng | Bên dùng | Quy tắc |
|---|---|---|
| `UI_DIR` + FastAPI phục vụ UI tĩnh | Dockerfile một image (Ngày 5) | Build frontend trong stage Node, copy `out/` vào image Python |
| `NEXT_PUBLIC_API_BASE` rỗng = cùng origin | Bản deploy | Không đổi |
| `CommentaryView.audio_url` (Ngày 5 thêm) | `AudioQueue` của Fan Player | Chỉ thêm trường |
| `RecapView.audio_url` (Ngày 5 thêm) | RecapPanel | Chỉ thêm trường |
| Message `on_air`, endpoint `/on-air` | Overlay trên bản deploy | Chỉ thêm trường |
| `/stats`, `/timeline`, `/events` | Có thể dùng lại logic cho MCP server (Ngày 6) | Không đổi tên |

---

## 17. Quyết định đã chốt (22/10/2026)

| # | Quyết định | Chốt |
|---|---|---|
| 1 | Điều khiển lower-third | ✅ **Tự động + biên tập viên can thiệp** (Take / Clear / Hold / Auto). Lập lịch tự động chạy ở client bằng hàm thuần; server giữ `OnAirState` và phát `on_air` |
| 2 | Ngôn ngữ giao diện | ✅ **Song ngữ, có nút chuyển.** Studio, overlay, so sánh và trang chọn trận mặc định EN; Fan mặc định VI. Nội dung theo hồ sơ người xem |
| 3 | Cách chạy frontend | ✅ **Next.js static export, FastAPI phục vụ:** một origin; một container ở Ngày 5; trang dùng query string |
| 4 | Phạm vi | ✅ **P0 + P1 + màn so sánh** |
| 5 | Pause | ✅ Pause chỉ dừng replay, không huỷ job studio (khác với `stop`) |
| 6 | Hồ sơ của Studio | ✅ `analyst` + ngôn ngữ UI; talking point hiện cả EN và VI |
| 7 | Thư viện | ✅ Không dùng thư viện biểu đồ / animation; dùng SVG + CSS |
| 8 | Restart | ✅ Phiên cũ phát `status.restarted` sau khi phiên mới đã đăng ký; mọi client tự kết nối lại |
| 9 | Overlay khi trận chưa tồn tại | ✅ Chờ và thử lại mỗi 5 s (Studio / Fan thì hiện nút "Tạo trận này") |
