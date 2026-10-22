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
- [ ] **P0** Tạo GitHub repo (public khi nộp), thêm `.gitignore`, `.env.example`, `README.md` khung
- [ ] **P0** Tạo **Azure Free Account** (thẻ Visa, 200 USD) làm subscription chính, xem `claude.md` mục 6.0
- [ ] **P0** Kích hoạt **Azure for Students** (email trường, 100 USD) làm dự phòng
- [ ] **P0** Đặt budget alert 50% / 80% trên subscription chính. **Không** nâng cấp lên Pay-As-You-Go
- [ ] **P0** Kiểm tra quota model trên Foundry của subscription vừa tạo. Nếu bị chặn, xử lý ngay trong ngày 0
- [ ] **P0** Tạo Resource Group, Microsoft Foundry project + deploy 1 model "mini"; gọi thử 1 request thành công
- [ ] **P0** Tạo Azure AI Speech; đọc thử 1 câu tiếng Việt bằng `vi-VN-HoaiMyNeural`
- [ ] **P1** Cài đủ công cụ local: Python 3.12, `uv`, Node 20+, Docker, Azure CLI, `azd`
- [ ] **P0** Chốt stack, model, region và ghi vào mục 8 của `claude.md`

**Hoàn thành khi:** gọi được Foundry và Speech từ máy local.

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
- [ ] **B2 (Foundry):** chạy lại `pundit eval --provider foundry` khi có Azure (GitHub Models đã ngừng 30/07/2026).

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
- [ ] **P0** Scoreboard + đồng hồ trận + nút điều khiển replay (play / pause / tốc độ)
- [ ] **P0** Feed sự kiện real-time
- [ ] **P0** Ô nhập claim + hiển thị verdict (màu theo kết quả) + số liệu
- [ ] **P0** Hàng đợi talking points: duyệt / bỏ / ghim
- [ ] **P0** Drawer **Why / Evidence / Agent Trace**
- [ ] **P1** Timeline momentum (biểu đồ)
- [ ] **P1** Sơ đồ sân hiển thị vị trí các event làm bằng chứng

**Overlay lower-third**
- [ ] **P0** Trang `/overlay` nền trong suốt, nhận JSON qua WebSocket, có animation vào / ra
- [ ] **P0** JSON overlay có timestamp trận đấu (`match_clock`, `show_at`, `duration`) để đồng bộ với video, theo yêu cầu "timed and machine-readable" của Rules
- [ ] **P1** Thử đưa vào OBS (Browser Source) để quay demo

**Fan VN Player**
- [ ] **P0** Chọn trận, hiển thị bình luận tiếng Việt dạng text theo thời gian thực
- [ ] **P0** Công tắc chế độ "Người mới xem"
- [ ] **P0** Màn hình chọn hồ sơ: Analyst / Casual, ngôn ngữ, CLB yêu thích
- [ ] **P1** Màn hình Player focus
- [ ] **P0** Nhãn **"Synthetic data"** trên mọi màn hình

**Hoàn thành khi:** chạy local thấy được đủ một luồng: claim, verdict, talking point, lower-third và bình luận tiếng Việt.

---

## Ngày 5 — 25/10: Azure Speech TTS + deploy lên Container Apps + App Insights

**Giọng đọc tiếng Việt**
- [ ] **P0** Tích hợp Azure AI Speech TTS cho bình luận real-time (stream audio về Fan VN Player)
- [ ] **P0** SSML: nhấn giọng / tăng tốc độ khi có bàn thắng hoặc tình huống nguy hiểm
- [ ] **P0** Recap audio (1–2 phút) lưu vào Blob Storage, có nút nghe lại
- [ ] **P1** Chọn giọng nam / nữ (`vi-VN-NamMinhNeural` / `vi-VN-HoaiMyNeural`)
- [ ] **P2** Speech-to-Text: bình luận viên nói claim để kiểm chứng

**Triển khai Azure**
- [ ] **P0** Dockerfile cho backend + frontend
- [ ] **P0** Bicep / `azd`: Container Apps Env, Container Registry, Storage, Key Vault, App Insights
- [ ] **P0** Managed Identity truy cập Foundry, Speech, Storage (không để key trong code)
- [ ] **P0** Deploy lên **Azure Container Apps**, có URL public, `min replicas = 1`
- [ ] **P0** Health endpoint `/healthz`
- [ ] **P0** App Insights: log + trace cho từng agent call, đo độ trễ
- [ ] **P1** GitHub Actions: test → build image → deploy

**Hoàn thành khi:** URL public chạy được demo đầy đủ, có audio tiếng Việt và log hiện trong App Insights.

---

## Ngày 6 — 26/10: Eval, tối ưu độ trễ, sửa lỗi, đánh bóng UI

**Eval**
- [ ] **P0** Mở rộng bộ eval lên 50–100 claim (cả 2–3 kịch bản)
- [ ] **P0** Đo: tỷ lệ verdict đúng, tỷ lệ số liệu bịa (mục tiêu **0%**)
- [ ] **P1** Eval chế độ "Người mới xem": giải thích đúng luật, kích hoạt đúng tình huống
- [ ] **P0** Ghi kết quả eval vào README (bảng số liệu)

**Độ trễ**
- [ ] **P0** Đo trên bản deploy: event → talking point (< 3s), event → audio (< 5s)
- [ ] **P1** Tối ưu: chạy song song agent, cache kết quả Stats Tools, rút gọn prompt, streaming TTS

**Ổn định & UI**
- [ ] **P0** Thử tắt Foundry → fallback hoạt động, UI không crash
- [ ] **P0** Sửa toàn bộ lỗi chặn demo
- [ ] **P1** Đánh bóng UI: màu, font, responsive, trạng thái loading / lỗi
- [ ] **P0** Kiểm tra bảo mật: không có secret trong repo / lịch sử git
- [ ] **P1** Đóng gói Stats Tools thành **MCP server** read-only, demo gọi thử từ GitHub Copilot (điểm cộng cho tiêu chí Agentic)
- [ ] **P1** Ghi lại cách đã dùng GitHub Copilot Agent Mode trong quá trình phát triển (hero technology)

**Hoàn thành khi:** demo chạy trơn 3 lần liên tiếp trên URL public, không lỗi.

---

## Ngày 7 — 27/10: Quay video, viết README, nộp bài

> Toàn bộ tài liệu nộp phải bằng **tiếng Anh** (Rules mục 4.9).

**README & pitch**
- [ ] **P0** Project pitch ngắn: làm gì, dùng công nghệ Microsoft / Azure nào, giải quyết vấn đề gì
- [ ] **P0** Vấn đề → giải pháp → người dùng (studio + fan toàn cầu, ví dụ fan Việt)
- [ ] **P0** Sơ đồ kiến trúc + bảng agent
- [ ] **P0** Công nghệ Microsoft đã dùng (Foundry, Agent Framework, Speech, Container Apps, App Insights…)
- [ ] **P0** Bảng mapping **5 tiêu chí chấm**: tính năng nào đáp ứng tiêu chí nào
- [ ] **P0** Hướng dẫn chạy local + `azd up`
- [ ] **P0** Kết quả eval + số đo độ trễ
- [ ] **P0** Ghi rõ: chỉ dùng dữ liệu tổng hợp, không liên kết với Premier League / CLB nào
- [ ] **P1** Ảnh chụp màn hình / GIF

**Video demo (bắt buộc DƯỚI 2 phút)**
- [ ] **P0** Kịch bản khoảng 1:50: vấn đề (10s) → claim sai bị bắt kèm bằng chứng (25s) → talking point + lower-third (20s) → Analyst và Casual xem cùng một moment (20s) → bình luận tiếng Việt có giọng đọc + "Người mới xem" (20s) → kiến trúc multi-agent trên Azure (15s) → kết (5s)
- [ ] **P0** Quay cảnh sản phẩm chạy thật trên bản deploy
- [ ] **P0** Phụ đề tiếng Anh cho mọi đoạn tiếng Việt
- [ ] **P0** Không dùng nhạc có bản quyền hay logo / thương hiệu bên thứ ba
- [ ] **P0** Upload **public** lên YouTube hoặc Vimeo

**Nộp bài**
- [ ] **P0** Repo public, link demo hoạt động, link video hoạt động
- [ ] **P0** Link demo không cần đăng nhập (hoặc ghi tài khoản test trong testing instructions)
- [ ] **P0** Chọn hạng mục: Grand Prize + **Best Multi-Agent System** (mỗi bài thắng tối đa 1 Grand + 1 Category)
- [ ] **P0** Điền form nộp bài trên Innovation Studio, **nộp trước 22:00 giờ VN 27/10** (dư thời gian xử lý sự cố)
- [ ] **P0** Chụp màn hình xác nhận đã nộp

---

## Sau khi nộp (27/10 → 10/11)

- [ ] Giữ URL demo chạy miễn phí, không giới hạn tới hết **10/11** (Rules mục 4.8); theo dõi credit Azure
- [ ] Sau 10/11: scale về 0 hoặc xoá Resource Group
- [ ] Người thắng được công bố chậm nhất 20/11. Nếu thắng, phải nộp giấy tờ xác minh trong 10 ngày làm việc

## Thứ tự cắt phạm vi khi trễ

Cắt theo thứ tự: STT (P2) → kịch bản 3 → sơ đồ sân → recap song ngữ → template lower-third phụ → CI/CD tự động.
**Không cắt:** Claim Check + Guardrail, bình luận tiếng Việt + TTS, deploy Azure, README + video.

## Chuyển sang ngày sau

| Ngày | Mục chưa xong | Lý do | Xử lý |
|---|---|---|---|
| | | | |
