# Checklist 7 ngày — Pundit Copilot

> Tổng quan dự án, kiến trúc và hạ tầng: xem [claude.md](claude.md).
> Ký hiệu ưu tiên: **P0** = bắt buộc để nộp · **P1** = nên có · **P2** = làm nếu còn thời gian.
> Cuối mỗi ngày: tick các mục đã xong, ghi mục chưa xong vào phần "Chuyển sang ngày sau".

**Mốc quan trọng**
- Đăng ký đóng: **20/10 12:00 PT** (≈ 02:00 ngày 21/10 giờ VN)
- Nộp bài: **27/10 23:59 PT** (≈ 13:59 ngày 28/10 giờ VN). Mục tiêu nộp sớm trước **27/10 22:00 giờ VN**.

---

## Ngày 0 — 20/10: Chuẩn bị

- [x] **P0** Đăng ký hackathon trên Innovation Studio trước hạn
- [x] **P0** Đọc Official Rules và cập nhật vào `claude.md` (mục 1.1)
- [x] **P0** Tạo GitHub repo, thêm `.gitignore`, `.env.example`, `README.md` khung (repo `Hoangnam25012004/epl_football_hackathon`, push 25/10; đặt public khi `security` xanh)
- [x] ~~**P0** Tạo Azure Free Account / Azure for Students, budget alert, quota Foundry, Resource Group, Foundry project + model, Azure AI Speech~~ **Huỷ (25/10): không có Azure.** Free Account bị từ chối ("not eligible", 20/10); thay bằng Hugging Face Space + model mở trong container + Foundry Local, xem [docs/day7-design.md](docs/day7-design.md) §0.1
- [x] **P1** Cài công cụ local: Python 3.12, `uv`, Node 22, Docker, Ollama (không cần Azure CLI / `azd`; `gh` chưa cài, thao tác GitHub làm trên web)
- [x] **P0** Chốt stack, model và ghi vào mục 8 của `claude.md` (không có region Azure)

**Hoàn thành khi:** ~~gọi được Foundry và Speech từ máy local~~ gọi được model thật từ máy local (Ollama, 21/10).

---

## Ngày 1 — 21/10: Schema + Synthetic Match Engine + Stats Tools + test

> Thiết kế chi tiết, thứ tự thực hiện và kế hoạch test: [docs/day1-design.md](docs/day1-design.md)

**Schema & dữ liệu**
- [x] **P0** Định nghĩa schema sự kiện (Pydantic + JSON Schema): `id`, `match_id`, `minute`, `second`, `period`, `team`, `player`, `type`, `x`, `y`, `outcome`, `related_ids`
- [x] **P0** Các loại sự kiện: pass (kể cả corner / throw-in / free kick… dạng `play_type`), shot, goal, pressure, tackle, interception, ball_recovery, foul, card, offside, substitution, VAR check, **ball_dead** (trạng thái bóng)
- [x] **P0** Máy trạng thái bóng (`ballstate.step`): bóng chết / lăn, đội giữ bóng, PossessionInterval
- [x] **P0** Danh sách đội / cầu thủ **tên giả** (không dùng tên thật)

**Synthetic Match Engine**
- [x] **P0** Sinh trận đấu theo `seed` (cùng seed thì ra cùng trận)
- [x] **P0** Replay theo thời gian thực có hệ số tốc độ (1x / 5x / 20x)
- [x] **P0** Kịch bản 1: *Đội A áp đảo hiệp 2 nhưng vẫn thua* (dùng cho demo Claim Check)
- [x] **P1** Kịch bản 2: lội ngược dòng, có tình huống việt vị + VAR (demo chế độ "Người mới xem")
- [x] **P2** Kịch bản 3: trận thẻ đỏ, đổi chiều momentum

**Match State & Stats Tools (deterministic)**
- [x] **P0** Match State Store: thống kê cộng dồn theo hiệp và theo cửa sổ thời gian (5', 15')
- [x] **P0** Các hàm: kiểm soát bóng **theo thời gian bóng lăn** (cập nhật live), cú sút / trúng đích, xG đơn giản, số lần vào 1/3 cuối sân, tỉ lệ chuyền thành công
- [x] **P0** Pass quality: cự ly, độ chính xác, độ khó đường chuyền (Rules gợi ý)
- [x] **P1** Tốc độ bóng / cú sút; quãng chạy và tốc độ cầu thủ, cảnh báo khi vượt ngưỡng
- [x] **P1** PPDA / pressing, chỉ số momentum, **control vs chaos**, nhịp trận
- [x] **P0** Mọi hàm trả về `value` + `evidence_event_ids`
- [x] **P0** Unit test cho từng hàm, chạy bằng `pytest`
- [x] **P1** Thiết lập GitHub Actions chạy test (đã có workflow; chạy khi đẩy repo lên GitHub)

**Hoàn thành khi:** chạy 1 lệnh là replay được trận mẫu ra console và các test đều pass.

---

## Ngày 2 — 22/10: Orchestrator + Claim Verifier + Guardrail trên Foundry

> Thiết kế chi tiết, thứ tự thực hiện và kế hoạch test: [docs/day2-design.md](docs/day2-design.md). Làm **mock trước**, sau đó dùng Ollama chạy local làm LLM thật tạm thời (GitHub Models đã ngừng 30/07/2026), và chuyển sang Foundry khi có Azure.

- [x] **P0** Khung Orchestrator: Microsoft Agent Framework 1.22 Workflows (9 executor, cạnh có điều kiện theo kiểu message). Adapter OpenAI-compatible dùng chung cho Ollama / Foundry; đã gọi model thật qua Ollama (`qwen2.5:3b`), **Foundry: chờ Azure**
- [x] **P0** Đăng ký Stats Tools thành tool / function cho agent (catalog `get_stat` / `compare_stat`, MAF `FunctionTool`, dùng chung cho EvidenceCollector, agent Ngày 3 và MCP Ngày 6)
- [x] **P0** Mock LLM + biến `USE_MOCK_LLM` để dev và test không tốn quota (kèm bơm lỗi `MOCK_LLM_FAULTS`)
- [x] **P0** **Moment Detector** (rule-based): bàn thắng, VAR huỷ bàn, thẻ, cơ hội lớn, cú sút mạnh, chuỗi tấn công nguy hiểm, momentum đổi chiều, thay người, hết hiệp
- [x] **P0** **Claim Verifier**: claim → tool calls → verdict `TRUE / PARTLY_TRUE / FALSE / INSUFFICIENT_DATA` + lý do + evidence
- [x] **P0** **Guardrail**: so mọi con số trong output với kết quả Stats Tools; chặn nếu sai hoặc thiếu `evidence`
- [x] **P0** Ghi **Agent Trace** (agent, tool, input, output, thời gian)
- [x] **P0** Shared match state + handoff có kiểu dữ liệu giữa các agent; retry / fallback khi một agent lỗi (tiêu chí của Best Multi-Agent System)
- [x] **P0** API: `POST /claims/verify`, WebSocket `/ws/match/{id}` đẩy event + moment
- [x] **P0** Fallback: khi Foundry lỗi hoặc timeout, trả template deterministic
- [x] **P1** Bắt đầu bộ eval: 20 claim mẫu có đáp án cho kịch bản 1 (+ 10 claim held-out)

**Hoàn thành khi** (2 tầng, xem day2-design §0):

- [x] **A (mock, bắt buộc):** claim *"Đội nhà áp đảo hiệp 2"* trên `dominant_loser-42` trả về TRUE, kèm số liệu (61% kiểm soát bóng, sút 15–1, xG 2.24–0.24) và event ID; mọi test xanh mà không cần mạng.
- [x] **B1 (LLM thật, local):** Ollama `qwen2.5:3b` (21/10): bộ eval 20 claim đúng 20/20, 0 verdict sai, held-out 10/10, không câu sai nào lọt Guardrail.
- [ ] **B2 (Foundry):** ~~`pundit eval --provider foundry` trên Azure~~ không có Azure; thay bằng eval với **Foundry Local** (`phi-4-mini`, cùng client OpenAI-compatible) ở Ngày 7 (C10, cần M3).

**Kết quả (20/10):** 1210 test xanh (không cần mạng), coverage 94,9%; eval mock: 20/20 đúng, 0 verdict sai trên cả 2 bộ, held-out 9/10 có verdict (claim còn lại được từ chối an toàn), p95 36 ms. Chi tiết: day2-design §19.

**Rà soát (21/10):**

- 1355 test xanh, coverage 96%; slow, perf và live đều xanh.
- Oracle test 4.652 claim: 100% đúng.
- Smoke test API thật: 25/25.
- Guardrail thêm 4 luật (`WRONG_COMPARISON`, `WRONG_ATTRIBUTION`, `FRAGMENT`, `UNGROUNDED`); kiểm tra spec đối chiếu cửa sổ thời gian với claim.
- CLI không còn traceback khi nhập sai; `.env` có BOM hoặc UTF-16 đọc được; sửa một test WebSocket bị treo do race.
- Chi tiết: day2-design §19.10.

---

## Ngày 3 — 23/10: Talking-Point + Graphics + Vietnamese Commentator

> Thiết kế chi tiết, thứ tự thực hiện và kế hoạch test: [docs/day3-design.md](docs/day3-design.md). **Template trước, LLM trong ngân sách** (chưa gắn Foundry: mock cho dev/CI, Ollama chạy offline).

**Talking-Point Agent**
- [x] **P0** Khi có moment, sinh 2–3 câu bình luận ngắn (tiếng Anh + tiếng Việt) kèm số liệu đã kiểm chứng
- [x] **P0** Đi qua Guardrail trước khi đẩy ra UI

**Graphics Agent**
- [x] **P0** Chọn chỉ số + template lower-third, xuất JSON: `title`, `subtitle`, `stats[]`, `team_colors`, `duration`
- [x] **P1** 3 template: so sánh 2 đội, chỉ số 1 cầu thủ, momentum

**Vietnamese Commentator Agent**
- [x] **P0** System prompt văn phong bình luận Việt + bảng thuật ngữ (lật cánh, phản công, pressing tầm cao…)
- [x] **P0** Sinh bình luận tiếng Việt real-time cho mỗi moment
- [x] **P0** Chế độ **"Người mới xem"**: thư viện giải thích luật (việt vị, VAR, phạt đền, thẻ, phạt góc), tự kích hoạt đúng tình huống
- [x] **P0** Recap cuối hiệp / cuối trận (văn bản)
- [x] **P1** Recap song ngữ Anh – Việt

**Personalization Agent (bắt buộc theo Rules)**
- [x] **P0** Hồ sơ người xem: chế độ Analyst / Casual, ngôn ngữ, CLB yêu thích
- [x] **P0** Cùng một moment, Analyst và Casual nhận output **khác biệt rõ ràng**
- [x] **P1** Chế độ **Player focus**: theo dõi một cầu thủ suốt trận
- [x] **P2** Chế độ "một chỉ số duy nhất" (người xem chọn chỉ số muốn theo dõi)

**API**
- [x] **P0** WebSocket đẩy thêm `talking_point`, `lower_third`, `commentary` (bình luận VI/EN theo hồ sơ; thay cho tên dự kiến `vn_commentary`), `recap`
- [x] **P0** `GET /matches/{id}/recap?lang=vi`

**Hoàn thành khi:** replay trận mẫu thì có talking points, JSON lower-third và bình luận tiếng Việt chạy ra liên tục, không có số liệu bịa.

**Kết quả (22/10):** đạt. 12/12 trận (4 kịch bản × 3 seed) PASS báo cáo studio: 0 rò rỉ, 0 lỗi oracle, D1–D4 100%, khoảng lặng ≤ 5'; ma trận lỗi 200 job 0 rò rỉ; smoke server thật 14/14; perf 9/9; chạy Ollama thật, đọc lại mọi câu LLM đã đăng và mọi bản nháp bị chặn qua 3 vòng, vá 15 lỗ hổng / chặn nhầm của Guardrail (mỗi phát hiện có test; luật tỷ số, phía momentum, chen tiếng Anh nằm ở `agents/narrative.py`). Chi tiết: [docs/day3-design.md](docs/day3-design.md) §23. Lưu ý máy dev: Windows chặn `.venv/Scripts/python.exe` (§23.2 dòng 18).

---

## Ngày 4 — 24/10: Studio Dashboard + overlay lower-third + Fan VN Player

> Thiết kế chi tiết, thứ tự thực hiện và kế hoạch test: [docs/day4-design.md](docs/day4-design.md). Next.js static export do FastAPI phục vụ (một origin); overlay tự động + biên tập viên Take / Clear / Hold; UI song ngữ; phạm vi P0 + P1 + màn so sánh Analyst / Casual.

**Studio Dashboard**
- [x] **P0** Scoreboard + đồng hồ trận + nút điều khiển replay (play / pause / tốc độ)
- [x] **P0** Feed sự kiện real-time
- [x] **P0** Ô nhập claim + hiển thị verdict (màu theo kết quả) + số liệu
- [x] **P0** Hàng đợi talking points: duyệt / bỏ / ghim
- [x] **P0** Drawer **Why / Evidence / Agent Trace**
- [x] **P1** Timeline momentum (biểu đồ)
- [x] **P1** Sơ đồ sân hiển thị vị trí các event làm bằng chứng

**Overlay lower-third**
- [x] **P0** Trang `/overlay` nền trong suốt, nhận JSON qua WebSocket, có animation vào / ra
- [x] **P0** JSON overlay có timestamp trận đấu (`match_clock`, `show_at`, `duration`) để đồng bộ với video, theo yêu cầu "timed and machine-readable" của Rules
- [x] **P1** Thử đưa vào OBS (Browser Source) để quay demo

**Fan VN Player**
- [x] **P0** Chọn trận, hiển thị bình luận tiếng Việt dạng text theo thời gian thực
- [x] **P0** Công tắc chế độ "Người mới xem"
- [x] **P0** Màn hình chọn hồ sơ: Analyst / Casual, ngôn ngữ, CLB yêu thích
- [x] **P1** Màn hình Player focus
- [x] **P0** Nhãn **"Synthetic data"** trên mọi màn hình

**Hoàn thành khi:** chạy local thấy được đủ một luồng: claim, verdict, talking point, lower-third và bình luận tiếng Việt.

**Kết quả (22/10):** đạt.

- **Đã xây:** Next.js 16 static export do `pundit serve --ui` phục vụ, gồm trang chọn trận, Studio, Overlay, Fan và màn So sánh Analyst / Casual (thêm ngoài checklist).
- **Kiểm thử:**
  - Vitest 73 passed (unit, property, component, contract).
  - Playwright 23/23: E1–E12 trên backend thật, cùng các ca mất kết nối và mã 4404 / 4422 / 4429 ở project mock.
  - axe 0 lỗi serious / critical trên 5 trang.
  - Cả trận 1000x khi mở 3 trang: long task lâu nhất 115 ms, heap tăng ≤ 2,3 MB; độ trễ p95 11 ms ở 20x.
  - Backend: `pytest -m "not live"` 1789 passed; ruff và mypy sạch.
- **OBS 32.2.2 (M3):** Browser Source không có CSS mặc định vẫn trong suốt trên nền chroma, lower-third tự lên rồi tự xuống sóng.
- **Lỗi tìm thấy và đã sửa (12):** biến thể `dark:` không theo theme Studio, tương phản màu, trang thiếu `?match=` mất nhãn Synthetic, Fan bị từ chối hồ sơ mà không biết lý do, vùng cuộn không dùng được bàn phím…
- Chi tiết: [docs/day4-design.md](docs/day4-design.md) §18.

---

## Ngày 5 — 25/10: Giọng đọc tiếng Việt + deploy (kế hoạch ban đầu: Azure Speech, Container Apps, App Insights)

> Phần giọng đọc: thiết kế chi tiết, thứ tự thực hiện và kế hoạch test ở [docs/day5-design.md](docs/day5-design.md). Làm **mock-first**: WAV tất định cho dev / CI, giọng của trình duyệt để nghe tiếng Việt; adapter Azure Speech có sẵn nhưng **không deploy** vì không có Azure. Trên Space: giọng trình duyệt, Piper nếu qua spike S2 (Ngày 7, C8).

**Giọng đọc tiếng Việt**
- [x] **P0** Tích hợp Azure AI Speech TTS cho bình luận real-time (stream audio về Fan VN Player): mỗi câu một clip qua `/audio`, Fan đọc lần lượt, bàn thắng chen ngang; adapter Azure REST + Managed Identity xong, chạy bằng mock và giọng trình duyệt, **Azure: chờ key**
- [x] **P0** SSML: nhấn giọng / tăng tốc độ khi có bàn thắng hoặc tình huống nguy hiểm (9 preset `prosody` + `break`, câu cảm thán đầu như "Vào!" nhanh và cao hơn; giọng vi-VN không có `emphasis`)
- [x] **P0** Recap audio (1–2 phút) lưu vào Blob Storage, có nút nghe lại (kho bền local ngay bây giờ; `BlobStore` đã chạy với Azurite; tua được, nghe lại được)
- [x] **P1** Chọn giọng nam / nữ (`vi-VN-NamMinhNeural` / `vi-VN-HoaiMyNeural`)
- [x] **P2** Speech-to-Text: bình luận viên nói claim để kiểm chứng (nút micro trong Studio, chữ chỉ điền vào ô claim, không tự gửi; mock + adapter Azure REST)

- Kết quả kiểm thử, khác biệt so với thiết kế và việc còn lại (tầng B Azure, nghe bằng tai): [docs/day5-design.md](docs/day5-design.md) §19.

**Triển khai**
- [x] **P0** Dockerfile cho backend + frontend (một image: FastAPI phục vụ UI tĩnh trên một origin; 542 MB, user không phải root, `HEALTHCHECK`; demo S1–S7, phiên riêng, `/mcp` chạy trên container; job CI `image` build + smoke)
- [x] **P0** Health endpoint `/healthz` (có từ Ngày 2; thêm `keepalive` và `build` ở Ngày 7)
- [x] ~~**P0** Bicep / `azd`, Managed Identity, Azure Container Apps, App Insights~~ **Không deploy (không có Azure).** `azure.yaml` + `infra/` (biên dịch sạch) và `deploy.yml` giữ trong repo, README ghi "Azure-ready, not deployed". Thay bằng Hugging Face Space ở Ngày 7 (C2, C3)
- [x] **P1** GitHub Actions: test → build image → deploy (`ci.yml` + `space.yml`: build → GHCR → Space → smoke; chạy lần đầu 25/10, xem Ngày 7)

**Hoàn thành khi:** URL public chạy được demo đầy đủ, có giọng đọc tiếng Việt. (Chuyển sang Ngày 7: Space.)

---

## Ngày 6 — 26/10: Eval, tối ưu độ trễ, sửa lỗi, đánh bóng UI

> Phần làm được khi chưa có Azure (MCP server, eval 100 claim, thử fallback, phiên riêng cho từng người xem, quét secret / tuân thủ, sửa lỗi và hướng dẫn demo): thiết kế chi tiết, thứ tự thực hiện và kế hoạch test ở [docs/day6-design.md](docs/day6-design.md). Phần đo trên bản deploy chờ Azure.

**Eval**
- [x] **P0** Mở rộng bộ eval lên 50–100 claim (cả 2–3 kịch bản) (100 claim / 3 trận: 72 có nhãn, 28 held-out đóng băng trước khi sửa parser; nhãn dựa trên số đếm độc lập; **bảng nhãn chờ bạn duyệt** (M4))
- [x] **P0** Đo: tỷ lệ verdict đúng, tỷ lệ số liệu bịa (mục tiêu **0%**) (mock: 96 % bộ có nhãn, 0 verdict sai, 0/274 số không nguồn, gate trong CI; Ollama `qwen2.5:3b`: 97 % bộ có nhãn, 0 verdict sai, held-out 75 %, 0/188 số không nguồn, sau bản sửa "cách đọc 'áp đảo' chỉ LLM đưa ra phải có từ áp đảo trong câu"; kiểm toán số độc lập với Guardrail)
- [x] **P1** Eval chế độ "Người mới xem": giải thích đúng luật, kích hoạt đúng tình huống (4 trận trong CI, 4 × 3 ở `-m slow`; giải thích ngay ở dòng đầu cần luật 17/18, trường hợp còn lại đến ở dòng kế tiếp cùng phút)
- [ ] **P0** Ghi kết quả eval vào README (bảng số liệu) (`docs/eval.md` tiếng Anh đã sẵn; chép vào README ở Ngày 7)

**Độ trễ**
- [x] **P0** Đo trên bản deploy (Space, 27/10, C10): event → talking point p95 55–62 ms ở 20x; event → audio: giọng trình duyệt đọc ngay trên máy người xem, server không tổng hợp clip (không có Azure / Piper)
- [ ] **P1** Tối ưu: chạy song song agent, cache kết quả Stats Tools, rút gọn prompt, streaming TTS (chưa cần: số đo local đạt; song song agent đã có từ Ngày 3)

**Ổn định & UI**
- [x] **P0** Thử tắt Foundry → fallback hoạt động, UI không crash (proxy bơm lỗi trước Ollama, cùng client với Foundry: C1–C13 13/13, 3 lượt liên tiếp; E2E `chaos` CH1–CH3; M6 tắt / bật Ollama thật giữa trận: fallback + chip + UI chạy tiếp; lần thử đầu sau khi bật lại hết giờ ở mặc định 90 s, phục hồi ngay khi `LLM_TIMEOUT_S=180` cho CPU này; Foundry thật ở tầng B)
- [x] **P0** Sửa toàn bộ lỗi chặn demo (bug bash: 0 blocker / major còn mở; demo-run S1–S7 3/3 liên tiếp trên bản build production)
- [x] **P1** Đánh bóng UI: màu, font, responsive, trạng thái loading / lỗi (hướng dẫn 3 bước, HelpDrawer, title, trạng thái panel, bố cục < 1280 px; 60 ảnh × axe)
- [x] **P0** Kiểm tra bảo mật: không có secret trong repo / lịch sử git (scanner của mình trên lịch sử + working tree + build + `.env`, tuân thủ tên thật, gitleaks, job CI `security`)
- [ ] **P1** Đóng gói Stats Tools thành **MCP server** read-only, demo gọi thử từ GitHub Copilot (điểm cộng cho tiêu chí Agentic) (MCP xong: stdio + `/mcp`, 9 tool; demo Copilot chờ M1)
- [ ] **P1** Ghi lại cách đã dùng GitHub Copilot Agent Mode trong quá trình phát triển (hero technology) (`docs/copilot.md` có cấu hình và 6 câu; chỉ ghi việc đã làm thật, hiện chưa có)

**Hoàn thành khi:** demo chạy trơn 3 lần liên tiếp trên URL public, không lỗi. (Bản local: 3/3 trên bản build production; URL public: Hugging Face Space, Ngày 7.)

---

## Ngày 7 — 27/10: Quay video, viết README, nộp bài

> Toàn bộ tài liệu nộp phải bằng **tiếng Anh** (Rules mục 4.9).
> Đã chốt không có Azure. Thiết kế phần còn lại, phân công Claude / bạn và hướng dẫn từng bước (Hugging Face Space, keepalive, Copilot, video, nộp bài): [docs/day7-design.md](docs/day7-design.md).

**Hạ tầng & tính năng bù khi không có Azure** (mã C = Claude, M = bạn; thiết kế: day7-design §0.5, as-built: §7)

- [x] **P0** C1 / S1: đo model trên 2 CPU (25/10). **Gate chưa đạt ở máy dev:** `qwen2.5:1.5b` p95 160 s (ngưỡng 45 s), 0 verdict sai; sửa lỗi llama.cpp chạy 8 luồng trên 2 CPU. Đo lại trên Space thật (C10)
- [x] **P0** C1 / S2: Piper (26/10). License: engine MIT (+ espeak-ng GPL, chạy process riêng), giọng `vais1000` CC BY 4.0 đạt, hai giọng còn lại bị loại. **RTF 1 CPU 0,77 > 0,5: gate không đạt ở máy dev** → C8 không làm, giữ giọng trình duyệt. S1 với `qwen2.5:0.5b`: p95 58 s, cũng không đạt → giữ 1.5b
- [x] **P0** C2: image Space (Ollama v0.40.2 CPU + model, uid 1000, `start.sh`, keepalive lớp 1 trong app; 2,58 GB, `/healthz` sau 26 s)
- [x] **P0** C3: `space.yml` (sau `ci` xanh: build → GHCR → Space theo digest → smoke). Lần chạy đầu: build + push xong, **Space PAUSED vì tài khoản Hugging Face mới có hạn mức cpu-basic = 0** → chờ M2b
- [x] **P0** M2b (27/10): Hugging Face PRO, 2 package GHCR public, Space RUNNING; `space.yml` tự deploy build `3a15622` sau `ci` xanh
- [x] **P0** C4: claim progressive (26/10): rev.1 theo luật ngay, rev.2 = workflow một lượt trên một worker; bất biến kiểm trên 100 claim eval; bật trên Space (`CLAIM_PROGRESSIVE=true`)
- [x] **P0** C5: luồng `agent_event` (WebSocket riêng `/ws/agents/{id}`) + trang `/agents` (26/10)
- [x] **P1** C6: Chaos theo phiên riêng (26/10): 6 lỗi, service + breaker riêng; "model nói dối": hơn 100 câu sai, 0 câu lên màn hình
- [ ] **P1** C7: ghi / phát lại phiên model bằng **Foundry Local** (`phi-4-mini`), nhãn "recorded" (cần M3)
- [x] **P0** M3 (27/10): Foundry Local 0.12.1 + `Phi-4-mini-instruct-generic-cpu:5` (bản GPU nạp lỗi trên máy này), gọi qua provider `openai`
- [x] ~~**P0** C8: Piper TTS provider~~ không làm: S2 không đạt gate tốc độ; giữ giọng trình duyệt (26/10)
- [x] **P0** C9: không còn chữ "mock" trên UI, nguồn câu chữ bằng lời, Agent Ops / Chaos trong "Thử gì", HelpDrawer (26/10). Còn: bộ ảnh `shots`, demo-run S1–S9 trên Space
- [x] **P0** C10 (27/10): eval 100 claim trên Space (96 %, 0 verdict sai, 1 số bịa "2-0" → luật `WRONG_OUTCOME`) và 20 claim với Foundry Local (100 %, 0 số bịa); rev.1 p95 0,3 s (1 người xem) / 1,16 s (3 người xem); **gate S1 trên Space không đạt** (p95 54,8 s), giữ model vì claim là progressive. Lộ thêm 2 lỗi: janitor dọn phiên chung đang được dùng qua API; lượt model bị Guardrail chặn bị báo "không kịp trả lời" (nay `rejected`). Chi tiết: [docs/eval.md](docs/eval.md), day7-design §7.4
- [ ] **P0** M4 + C12: watchdog (lớp 2) bằng Copilot Agent Mode, Claude review · M5: UptimeRobot (lớp 3)
- [ ] **P0** M6 + C13: duyệt bảng nhãn eval, chạy gate
- [x] **P1** Thêm file `LICENSE` (MIT, Dang Hoang Nam, 26/10)
- [ ] **P2** Đổi tên repo thành `pundit-copilot` (tuỳ bạn; "EPL" nằm trong danh sách tên thật cần tránh của dự án)

**README & pitch**

> Nháp ngày 24/10: `README.md` (tiếng Anh, cột trạng thái chỉ ghi việc đã chạy thật), `docs/pitch.md`, `docs/video/script.md` (1:55), `docs/video/subtitles.srt`. Viết lại 26/10 theo hướng không có Azure (C11): pitch, kịch bản (progressive, Agent Ops, Chaos), `docs/video/voiceover.txt`, phụ đề, README. Còn: link demo / video, số đo trên Space, `docs/submission.md`.

- [ ] **P0** Project pitch ngắn: làm gì, dùng công nghệ Microsoft nào, giải quyết vấn đề gì
- [ ] **P0** Vấn đề → giải pháp → người dùng (studio + fan toàn cầu, ví dụ fan Việt)
- [ ] **P0** Sơ đồ kiến trúc + bảng agent
- [ ] **P0** Công nghệ Microsoft đã dùng, ghi đúng sự thật: Agent Framework, Foundry Local, GitHub Actions / GHCR, Copilot Agent Mode, MCP; Foundry cloud, Azure AI Speech, Bicep / `azd`: "Azure-ready, not deployed"
- [ ] **P0** Bảng mapping **5 tiêu chí chấm**: tính năng nào đáp ứng tiêu chí nào
- [ ] **P0** Hướng dẫn chạy local + image Space (Docker)
- [ ] **P0** Kết quả eval + số đo độ trễ (trên Space)
- [ ] **P0** Ghi rõ: chỉ dùng dữ liệu tổng hợp, không liên kết với Premier League / CLB nào
- [ ] **P1** Ảnh chụp màn hình / GIF

**Video demo (bắt buộc DƯỚI 2 phút)**
- [x] **P0** (nháp `docs/video/script.md`, 1:55; sửa đoạn kiến trúc theo Space + Foundry Local) Kịch bản khoảng 1:50: vấn đề (10s) → claim sai bị bắt kèm bằng chứng (25s) → talking point + lower-third (20s) → Analyst và Casual xem cùng một moment (20s) → bình luận tiếng Việt có giọng đọc + "Người mới xem" (20s) → kiến trúc multi-agent (Agent Framework, một container CPU, Foundry Local) (15s) → kết (5s)
- [ ] **P0** Quay cảnh sản phẩm chạy thật trên bản deploy
- [ ] **P0** Phụ đề tiếng Anh cho mọi đoạn tiếng Việt
- [ ] **P0** Không dùng nhạc có bản quyền hay logo / thương hiệu bên thứ ba
- [ ] **P0** Upload **public** lên YouTube hoặc Vimeo

**Nộp bài**
- [ ] **P0** Repo public, link demo hoạt động, link video hoạt động (build UI với `NEXT_PUBLIC_REPO_URL` và `NEXT_PUBLIC_VIDEO_URL` để trang đầu có link thay cho "sắp có"; job CI `security` + gate eval xanh trước khi push public)
- [ ] **P0** Link demo không cần đăng nhập (hoặc ghi tài khoản test trong testing instructions)
- [ ] **P0** Chọn hạng mục: Grand Prize + **Best Multi-Agent System** (mỗi bài thắng tối đa 1 Grand + 1 Category)
- [ ] **P0** Điền form nộp bài trên Innovation Studio, **nộp trước 22:00 giờ VN 27/10** (dư thời gian xử lý sự cố)
- [ ] **P0** Chụp màn hình xác nhận đã nộp

---

## Sau khi nộp (27/10 → 10/11)

- [ ] Giữ URL demo chạy miễn phí, không giới hạn tới hết **10/11** (Rules mục 4.8): Space + 3 lớp keepalive; xem issue do watchdog mở
- [ ] Sau 10/11: pause Space, tắt workflow watchdog, xoá token `github-actions-pundit`, pause UptimeRobot (day7-design §3)
- [ ] Người thắng được công bố chậm nhất 20/11. Nếu thắng, phải nộp giấy tờ xác minh trong 10 ngày làm việc

## Thứ tự cắt phạm vi khi trễ

Cắt theo thứ tự: STT (P2) → kịch bản 3 → sơ đồ sân → recap song ngữ → template lower-third phụ → CI/CD tự động.
**Không cắt:** Claim Check + Guardrail, bình luận tiếng Việt + giọng đọc, Space + keepalive, claim progressive, Agent Ops tối thiểu, README + video. Thứ tự cắt riêng của Ngày 7: day7-design §4.

## Chuyển sang ngày sau

| Ngày | Mục chưa xong | Lý do | Xử lý |
|---|---|---|---|
| 5 → 7 | Deploy URL public | Không có Azure | Hugging Face Space (C2, C3 xong; chờ hạn mức cpu-basic) |
| 5 → 7 | Giọng neural tiếng Việt trên server | Không có Azure Speech | Piper nếu qua S2, không thì giọng trình duyệt |
| 6 → 7 | Đo độ trễ trên bản deploy | Chưa có URL public | C10 trên Space |
