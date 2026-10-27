# Pundit Copilot — Microsoft × Premier League "Inside the Game" Hackathon

> File này mô tả tổng quan dự án, phạm vi, hạng mục công việc và hạ tầng.
> Claude và mọi thành viên đọc file này trước khi bắt đầu làm việc.

## 1. Thông tin hackathon

> Nguồn chính thức: repo [microsoft/insidethegamehackathon](https://github.com/microsoft/insidethegamehackathon), file `OFFICIAL RULES.md`.

| Mục | Chi tiết |
|---|---|
| Tên | Inside the Game: Developer Hackathon (Microsoft × Premier League) |
| Đề bài | Dùng AI biến **dữ liệu sự kiện bóng đá tổng hợp (synthetic)** thành thông tin trận đấu có giải thích được (explainable match intelligence), bình luận real-time và recap tự động cho studio / broadcast / streaming, đủ linh hoạt để fan ở **mọi thị trường** tự tuỳ biến trải nghiệm |
| Hình thức | **Làm một mình** (luật cho phép đội tối đa 4 người) |
| Thời gian làm bài | 06/10 – 27/10/2026 |
| **Hạn nộp** | **27/10/2026 23:59 PT** (≈ 13:59 ngày 28/10 giờ VN) |
| Thời gian chấm | 27/10 – **10/11/2026**. Demo phải chạy miễn phí, không giới hạn cho tới hết ngày này |
| Công bố người thắng | Chậm nhất 20/11/2026 |
| Giải thưởng (cho 1 người) | Grand 1st: 4.000 USD + vé xem 1 trận Premier League + voucher 50 USD · Grand 2nd: 2.500 USD + vé + voucher · Mỗi Category Prize: 1.500 USD + voucher |
| Hạng mục giải | Grand Prize (1st, 2nd) · Best Use of Microsoft Foundry · Best Enterprise Solution · Best Multi-Agent System · Best Azure Cloud Native Integration. **Mỗi bài thắng tối đa 1 Grand + 1 Category** |
| Chấm vòng 1 | Pass/fail: đúng chủ đề và có dùng các công nghệ chính (hero technologies) |
| Chấm vòng 2 (mỗi tiêu chí 20%) | Technological Implementation · Agentic Design & Innovation · Real-World Impact & Applicability · User Experience & Presentation · Adherence to Hackathon Category |

**Ràng buộc bắt buộc:** chỉ dùng **dữ liệu tổng hợp**. Tuyệt đối không dùng dữ liệu, tên cầu thủ hay logo thật của Premier League / CLB. Mọi màn hình phải có nhãn "Synthetic data".

### 1.1. Yêu cầu từ Official Rules ảnh hưởng trực tiếp tới dự án

- **Pipeline 5 bước bắt buộc:** Ingest → Interpret → Explain → Render → Personalize. Rules nói rõ: chỉ có số liệu mà không có khán giả thì chưa đủ, chỉ cá nhân hoá mà không có gì để cá nhân hoá cũng chưa đủ.
- **Personalize:** cùng một dữ liệu phải tạo ra **hai trải nghiệm thật sự khác nhau** cho analyst và fan bình thường. Gợi ý từ ban tổ chức: CLB yêu thích, cầu thủ yêu thích, chế độ theo dõi một cầu thủ suốt trận, ngôn ngữ, một chỉ số duy nhất người xem quan tâm.
- **Render:** output phải **có timestamp và máy đọc được** để render thành overlay đồng bộ với video trận đấu.
- **Tính năng nên có:** gắn tên cầu thủ, chất lượng đường chuyền (cự ly, độ chính xác, độ khó), tốc độ bóng / cú sút, giải thích "control vs chaos", thay đổi áp lực chiến thuật, nhịp trận.
- **Data innovation:** tự tạo bộ dữ liệu tổng hợp giống thật. Bộ dữ liệu này được chấm trong tiêu chí Technological Implementation ("optimized synthetic data creation").
- **Hero technologies:** Microsoft Foundry, Agent Framework, Azure MCP, GitHub Copilot Agent Mode, Fabric, GitHub SDK/CLI, Azure Apps & AI Services, Azure databases. Tiêu chí Agentic nhắc rõ **MCP integration** và **multi-agent collaboration**.
- **Best Multi-Agent System** chấm: agent có vai trò riêng biệt, phối hợp qua **shared state** và **handoff**, **phục hồi khi lỗi**, đạt kết quả mà một agent đơn lẻ khó làm được.
- **Bài nộp gồm:** pitch ngắn (làm gì, dùng công nghệ Microsoft nào, giải quyết vấn đề gì) · **video demo < 2 phút** (giám khảo không bắt buộc xem quá 2 phút), đăng public trên YouTube / Vimeo · **GitHub repo public** · link demo dùng được (nếu cần đăng nhập thì phải cung cấp tài khoản).
- **Ngôn ngữ:** mọi tài liệu nộp phải bằng **tiếng Anh**. Phần tiếng Việt trong video phải có phụ đề hoặc bản dịch tiếng Anh.
- **Video:** không dùng nhạc có bản quyền hay thương hiệu bên thứ ba.
- **Lưu ý:** giám khảo có thể không tự chạy thử mà chỉ chấm qua mô tả, ảnh và video. Vì vậy **video là sản phẩm quan trọng nhất**.

## 2. Tổng quan đề tài

**Pundit Copilot** là trợ lý AI chạy real-time trong trận đấu, phục vụ hai nhóm người dùng.

### 2.1. Phần A: Copilot cho bình luận viên & biên tập viên studio (đề tài chính)
- **Kiểm chứng phát biểu (Claim Check):** bình luận viên nói hoặc gõ một nhận định, ví dụ *"Đội A áp đảo hiệp 2"*. Hệ thống trả về verdict **Đúng / Sai một phần / Sai / Không đủ dữ liệu** kèm số liệu bằng chứng (kiểm soát bóng, số cú sút, xG, số lần vào vòng cấm…) và danh sách event ID làm nguồn.
- **Gợi ý câu nói (Talking Points):** cứ mỗi khi có khoảnh khắc quan trọng (bàn thắng, thẻ, chuỗi pressing, momentum đổi chiều), hệ thống đề xuất 2–3 câu bình luận ngắn, có số liệu đã kiểm chứng.
- **Đồ họa lower-third:** sinh dữ liệu đồ họa (tiêu đề, chỉ số, so sánh) dạng JSON và render thành overlay HTML để studio chèn lên sóng.
- **Bảng điều khiển biên tập viên:** feed sự kiện, timeline momentum, hàng đợi gợi ý cho biên tập viên duyệt / bỏ / ghim.

### 2.2. Phần B: Bình luận & recap tiếng Việt có giọng đọc
- **Bình luận tiếng Việt real-time:** chuyển sự kiện thành câu bình luận tiếng Việt tự nhiên, rồi đọc thành tiếng. Kế hoạch ban đầu là **Azure AI Speech** (giọng neural `vi-VN-HoaiMyNeural` / `vi-VN-NamMinhNeural`); adapter đã viết nhưng **không có Azure (chốt 25/10)**, nên bản public đọc bằng **Piper** trong container nếu qua spike S2, dự phòng là giọng của trình duyệt (xem mục 6).
- **Văn phong cho fan Việt:** dùng thuật ngữ quen thuộc ("lật cánh", "phản công", "pressing tầm cao"…) và có chế độ **"Người mới xem"** tự giải thích luật (việt vị, VAR, phạt đền…) khi tình huống xảy ra.
- **Recap tự động:** cuối hiệp và cuối trận sinh bản tóm tắt dạng văn bản và audio (1–2 phút), kèm các khoảnh khắc then chốt.
- **Nhận lệnh bằng giọng nói (tuỳ chọn):** dùng Speech-to-Text tiếng Việt / tiếng Anh để bình luận viên nói claim cần kiểm chứng.

### 2.3. Cá nhân hoá (Personalize, bắt buộc theo Rules)

| Chế độ | Người dùng | Khác biệt |
|---|---|---|
| **Analyst** | Bình luận viên, chuyên gia | Số liệu dày: xG, PPDA, pass difficulty, momentum, evidence đầy đủ |
| **Casual / Người mới xem** | Fan bình thường | Câu chữ đơn giản, ít số, tự giải thích luật |
| **CLB yêu thích** | Fan một đội | Bình luận nghiêng về góc nhìn đội đó, ưu tiên khoảnh khắc của đội đó |
| **Player focus** | Fan một cầu thủ | Theo dõi một cầu thủ suốt trận: chạm bóng, chuyền, quãng chạy, mốc thành tích |
| **Ngôn ngữ** | Fan toàn cầu | Tiếng Việt (có giọng đọc) + tiếng Anh; kiến trúc sẵn sàng thêm ngôn ngữ khác |

### 2.4. Nguyên tắc cốt lõi: "Không có bằng chứng thì không nói"
- Mọi số liệu trong câu trả lời của LLM **phải lấy từ tool tính toán deterministic**. LLM không được tự bịa con số.
- Mọi phát biểu sinh ra đều kèm danh sách `evidence` (event IDs + chỉ số). Verifier Agent có quyền **bác bỏ** câu nào thiếu bằng chứng.
- Thông điệp đi thi: *"Copilot giúp đài truyền hình không bao giờ nói sai số liệu khi đang lên sóng, và đưa Premier League tới fan Việt bằng chính ngôn ngữ của họ."*

## 3. Kiến trúc

```
[Synthetic Match Engine] --events--> [Event Bus / Stream]
                                          |
                                          v
                               [Match State Store] (thống kê cộng dồn, momentum)
                                          |
          +-------------------------------+-------------------------------+
          v                               v                               v
   [Stats Tools (deterministic)]   [Orchestrator - Microsoft Agent Framework / Foundry Agents]
                                          |
         +-------------+-----------------+----------------+----------------+
         v             v                 v                v                v
   Moment Detector  Claim Verifier  Talking-Point     Graphics Agent   Vietnamese
   Agent            Agent           Agent             (lower-third)    Commentator Agent
                                          |                                  |
                                          v                                  v
                               [Verifier / Guardrail]               [Azure AI Speech TTS]
                                          |                                  |
                                          +---------------+------------------+
                                                          v
                                   [API + WebSocket/SignalR] --> [Studio Dashboard UI]
                                                             --> [Lower-third Overlay page]
                                                             --> [Fan VN Player (audio + text)]
```

> **Từ 25/10 (không có Azure):** model của agent là Ollama trong container (bản public, chỉ cho claim) hoặc **Microsoft Foundry Local** (ghi phiên model + eval), cùng client OpenAI-compatible với Foundry cloud; `[Azure AI Speech TTS]` thay bằng Piper / giọng trình duyệt; WebSocket của FastAPI, không SignalR. Kiến trúc đích của bản public: [docs/day7-design.md](docs/day7-design.md) §0.2.

### Các agent

| Agent | Vai trò | Dùng LLM? |
|---|---|---|
| Moment Detector | Phát hiện khoảnh khắc quan trọng (bàn thắng, đổi momentum, chuỗi nguy hiểm) | Không (rule-based) |
| Claim Verifier | Tách claim thành truy vấn chỉ số, gọi Stats Tools, ra verdict | Có (phân tích claim) + tool |
| Talking-Point | Sinh câu bình luận ngắn kèm số liệu | Có |
| Graphics | Chọn chỉ số và template lower-third, xuất JSON | Có (nhẹ) |
| Vietnamese Commentator | Viết bình luận / recap tiếng Việt, chế độ "Người mới xem" | Có |
| Personalization | Điều chỉnh output theo hồ sơ người xem (Analyst / Casual / CLB / cầu thủ / ngôn ngữ) | Có |
| Guardrail / Verifier | (1) Mọi con số trong output phải khớp với Stats Tools, sai thì bác bỏ. (2) Kiểm tra nội dung an toàn: không xúc phạm, phân biệt đối xử hay hạ thấp cầu thủ / CLB / trọng tài, theo Official Rules §4.4 và Microsoft Event Code of Conduct. Bộ lọc content safety của Foundry dùng được khi chạy trên Foundry cloud; bản public (model mở, không có Azure) chỉ dựa vào luật của Guardrail | Không (deterministic) + content filter (khi có Foundry cloud) + LLM-judge tuỳ chọn |

**Yêu cầu cho hạng mục Multi-Agent:** các agent đọc và ghi qua một **shared match state**, handoff có cấu trúc (typed message), có retry / fallback khi một agent lỗi, và hiển thị toàn bộ trace trên UI.

**MCP server:** đóng gói Stats Tools thành một MCP server (read-only) để agent và client AI bên ngoài (VD GitHub Copilot, Claude, hệ thống của đài) dùng chung. Server này đáp ứng ý "MCP integration" trong tiêu chí Agentic.

## 4. Tech stack đề xuất

> Mặc định bên dưới; đổi nếu đội mạnh về stack khác. Ghi lại quyết định vào mục 8.

- **Backend / Agents:** Python 3.12 (quản lý bằng `uv`), FastAPI, Microsoft Agent Framework. Model qua một client OpenAI-compatible dùng chung cho Ollama, **Microsoft Foundry Local** và Foundry cloud (Foundry cloud: viết xong, không deploy).
- **Frontend:** Next.js (TypeScript) + Tailwind; overlay lower-third là trang HTML riêng có nền trong suốt để đưa vào OBS / vMix.
- **Real-time:** WebSocket (FastAPI).
- **Data contracts:** Pydantic models + JSON Schema dùng chung.
- **Test / CI:** pytest, Vitest, Playwright, GitHub Actions.
- **Deploy:** một image Docker trên **Hugging Face Space**, build bằng GitHub Actions → GHCR (`space.yml`). Bicep / `azd` giữ trong repo, ghi "Azure-ready, not deployed".

## 5. Hạng mục công việc

### 5.1. Dữ liệu & engine (P0)
- [ ] Định nghĩa schema sự kiện (pass, shot, tackle, foul, card, sub, goal, offside, VAR…) gồm tọa độ, thời điểm, đội, cầu thủ (tên giả).
- [ ] Synthetic Match Engine: sinh trận đấu có **seed** (tái lập được), phát lại theo thời gian thực có hệ số tốc độ (1x, 5x, 20x).
- [ ] 2–3 kịch bản trận mẫu có "câu chuyện" rõ ràng (VD đội A áp đảo nhưng thua, lội ngược dòng, có tình huống việt vị và VAR).
- [ ] Match State Store: thống kê cộng dồn theo hiệp và theo cửa sổ thời gian (5', 15'), cùng chỉ số momentum.

### 5.2. Stats Tools deterministic (P0)
- [ ] Các hàm: kiểm soát bóng (theo thời gian bóng lăn), số cú sút / trúng đích, xG đơn giản, số lần vào 1/3 cuối sân, PPDA / pressing, đường chuyền thành công, so sánh hai đội theo khoảng thời gian.
- [ ] Theo gợi ý của Rules: **pass quality** (cự ly, độ chính xác, độ khó), **tốc độ bóng / cú sút**, quãng chạy và tốc độ cầu thủ (cảnh báo khi vượt ngưỡng).
- [ ] Chỉ số giải thích: **control vs chaos**, thay đổi áp lực chiến thuật, nhịp trận (game rhythm).
- [ ] Mỗi hàm trả về `value` + `evidence_event_ids`.
- [ ] Unit test cho từng hàm.
- [ ] (P1) Đóng gói các hàm thành **MCP server** read-only.

### 5.3. Agents & orchestration (P0)
- [ ] Orchestrator trên Microsoft Agent Framework, kết nối model thật (không chỉ có mock): Ollama trong container cho bản public, **Microsoft Foundry Local** cho ghi phiên và eval (Foundry cloud cần Azure, không có).
- [ ] Claim Verifier: chuyển claim sang tool call, ra verdict có lý do.
- [ ] Talking-Point Agent + Graphics Agent.
- [ ] Guardrail kiểm tra số liệu và bác bỏ output sai.
- [ ] Ghi **Agent Trace** (agent nào gọi tool nào, kết quả ra sao) để hiển thị trên UI, phục vụ tiêu chí "explainable".

### 5.4. Tiếng Việt & giọng đọc (P0 cho text, P1 cho voice)
- [ ] Vietnamese Commentator Agent với system prompt văn phong bình luận Việt và bảng thuật ngữ.
- [ ] Chế độ "Người mới xem": thư viện giải thích luật ngắn gọn, tự kích hoạt khi tình huống liên quan xảy ra.
- [ ] Giọng đọc với SSML / prosody (nhấn giọng khi có bàn thắng, tốc độ đọc thay đổi theo mức kịch tính): Piper trong container hoặc giọng trình duyệt; adapter Azure AI Speech có sẵn, không deploy.
- [ ] Recap cuối hiệp / cuối trận gồm văn bản + file audio.
- [ ] (P2) Speech-to-Text để nhận claim bằng giọng nói.

### 5.5. Giao diện (P0)
- [ ] Studio Dashboard: feed sự kiện, scoreboard, timeline momentum, ô nhập claim, hàng đợi talking points (duyệt / bỏ / ghim), drawer "Why / Evidence / Agent Trace".
- [ ] Trang overlay lower-third (nền trong suốt, animation vào ra).
- [ ] Fan VN Player: chọn trận, nghe bình luận tiếng Việt, bật / tắt chế độ "Người mới xem".
- [ ] Nhãn "Synthetic data" trên mọi màn hình.

### 5.6. Hạ tầng & triển khai (P0)
- [ ] Dockerfile cho backend và frontend (một image), cộng image Space (Ollama + model, uid 1000).
- [ ] Deploy lên **Hugging Face Space** với URL public để giám khảo dùng thử, không cần đăng nhập, chạy tới hết 10/11 (keepalive 3 lớp).
- [ ] Health endpoint (`/healthz` có `keepalive`, `build`), log có cấu trúc. (App Insights cần Azure: không làm.)
- [ ] GitHub Actions: test → build image → GHCR → Space → smoke.
- [ ] Bicep / `azd` giữ trong repo, ghi "Azure-ready, not deployed".

### 5.7. Đánh giá chất lượng (P1)
- [ ] Bộ eval khoảng 50–100 claim mẫu có đáp án sẵn; đo tỷ lệ verdict đúng và tỷ lệ "hallucinated number" (mục tiêu 0%).
- [ ] Đo độ trễ từ lúc có event tới lúc có gợi ý (mục tiêu < 3 giây) và từ lúc có event tới lúc phát audio.

### 5.8. Nộp bài (P0)
- [ ] README **bằng tiếng Anh**: vấn đề, giải pháp, kiến trúc, cách chạy, công nghệ Microsoft đã dùng, mapping theo tiêu chí chấm.
- [ ] Project pitch ngắn bằng tiếng Anh.
- [ ] Video demo **dưới 2 phút**, có phụ đề tiếng Anh cho phần tiếng Việt, không dùng nhạc có bản quyền.
- [ ] Hạng mục dự thi: **Grand Prize** + **Best Multi-Agent System** (đã chốt).
- [ ] Kiểm tra lại: không có dữ liệu thật, không có secret trong repo, repo public, link demo không cần đăng nhập.

## 6. Yêu cầu hạ tầng

> **Đã chốt 25/10: không có Azure.** Azure Free Account bằng email trường bị từ chối ("not eligible", 20/10), không có subscription thay thế kịp hạn. Bản public chạy trên **Hugging Face Space**. Thiết kế đầy đủ và hướng dẫn từng bước: [docs/day7-design.md](docs/day7-design.md). Phương án Azure ban đầu (Foundry, Speech, Container Apps, App Insights, Key Vault) vẫn nằm trong repo dưới dạng Bicep / `azd` (`azure.yaml`, `infra/`, `deploy.yml`, biên dịch sạch) và được ghi "Azure-ready, not deployed".

### 6.0. Tài khoản và dịch vụ

| Dịch vụ | Dùng cho | Ghi chú |
|---|---|---|
| **GitHub** (`Hoangnam25012004/epl_football_hackathon`) | Mã nguồn, CI, deploy (`ci.yml`, `space.yml`), watchdog keepalive (lớp 2) | Public khi job `security` xanh (Actions miễn phí không giới hạn phút cho repo public) |
| **GHCR** | Image chính `pundit-copilot` + image Space `pundit-copilot-space`, tag `sha-<commit>` + `latest` | Package phải **public** để Space kéo được image |
| **Hugging Face Space** (`hnam2501/pundit-copilot`, Docker) | Demo public `https://hnam2501-pundit-copilot.hf.space` | CPU basic 2 vCPU / 16 GB. Tài khoản mới có hạn mức cpu-basic = 0 (25/10): cần PRO hoặc tài khoản cũ hơn |
| **Microsoft Foundry Local** (máy dev) | Ghi phiên model cho Studio (phát lại trên Space, nhãn "recorded") + eval | `phi-4-mini`, cùng client OpenAI-compatible với Foundry cloud |
| **Ollama** (trong container Space và máy dev) | Model live cho claim trên Space | v0.40.2, `qwen2.5:1.5b` mặc định, model chốt bằng spike S1 |
| **UptimeRobot** | Keepalive lớp 3, email khi Space sập | Miễn phí |

### 6.1. Thành phần của bản public

| Thành phần | Mục đích | Ghi chú |
|---|---|---|
| Một container (`deploy/space/`) | API + UI tĩnh + `/mcp` + WebSocket + Ollama | uid 1000, cổng 8000; `start.sh` khởi động Ollama (tự restart) rồi `pundit serve` |
| Model trên CPU | Chỉ cho luồng claim (`LLM_LIVE_SCOPE=claims`) | Studio / recap dùng template hoặc phiên Foundry Local đã ghi; claim progressive (rev.1 theo luật < 1 s) |
| Giọng đọc | Bình luận + recap tiếng Việt | Piper nếu qua S2, dự phòng giọng trình duyệt (`TTS_PROVIDER=browser`) |
| Keepalive 3 lớp | Space không ngủ khi không ai xem | (1) app tự ping URL public mỗi 20' · (2) watchdog GitHub Actions restart Space bằng HF token · (3) UptimeRobot |
| Lưu trữ | Match state, clip audio | Trong bộ nhớ / đĩa của container; trận mẫu có seed cố định nên tái lập được |

### 6.2. Yêu cầu phi chức năng
- **Độ trễ:** event tới talking point < 3s; event tới audio tiếng Việt < 5s; claim rev.1 < 1 s, rev.2 (model trên CPU) trong ngân sách và có tiến trình.
- **Độ tin cậy:** khi model lỗi, chậm hoặc Ollama chết, fallback sang luật / template deterministic, UI vẫn chạy (không được crash khi giám khảo đang thử).
- **Bảo mật:** không commit key; secret chỉ ở GitHub (`HF_TOKEN`); image và repo của Space không chứa secret; `.env.example` chỉ chứa tên biến.
- **Tái lập:** `space.yml` dựng lại toàn bộ bản public từ một commit; trận mẫu có seed cố định.
- **Chi phí:** 0 USD (trừ Hugging Face PRO nếu cần để có hạn mức CPU). Sau 10/11: pause Space, tắt watchdog, xoá token.

### 6.3. Môi trường phát triển local
- Python 3.12 + `uv`, Node.js 22, Docker Desktop, Ollama, Foundry Local, Git. Không cần Azure CLI / `azd`; `gh` chưa cài (thao tác GitHub làm trên web).
- Chạy được offline bằng **mock LLM** (để dev và test không tốn tài nguyên), bật model thật bằng biến môi trường.
- Biến môi trường chính: `USE_MOCK_LLM`, `LLM_PROVIDER` (`ollama` / `openai` / `foundry`), `LLM_MODEL`, `LLM_BASE_URL`, `LLM_LIVE_SCOPE`, `LLM_TIMEOUT_S`, `CLAIM_BUDGET_S`, `TTS_PROVIDER`, `KEEPALIVE_URL`, `PUNDIT_BUILD`. Giá trị mặc định của Space: `deploy/space/env.space`.

## 7. Lịch 7 ngày (20/10 → 27/10/2026)

| Ngày | Mục tiêu |
|---|---|
| 21/10 | Schema + Synthetic Match Engine + Stats Tools + test |
| 22/10 | Orchestrator + Claim Verifier + Guardrail (mock, rồi Ollama local) |
| 23/10 | Talking-Point + Graphics + Vietnamese Commentator |
| 24/10 | Studio Dashboard + overlay lower-third + Fan VN Player |
| 25/10 | Giọng đọc (mock + giọng trình duyệt) + Docker; thiết kế lại Ngày 7 khi chắc chắn không có Azure |
| 26/10 | Hugging Face Space + keepalive, claim progressive, Agent Ops, Foundry Local, Piper, eval trên Space (day7-design §0.4–0.5) |
| 27/10 | Quay video, viết README, nộp bài (trước 23:59 PT) |

**Nguyên tắc cắt phạm vi khi trễ:** giữ Claim Check + Guardrail + tiếng Việt TTS + deploy. Bỏ STT, Web PubSub, Cosmos, Event Hubs trước.

## 8. Quy ước làm việc (dành cho Claude & thành viên)
- Ngôn ngữ trao đổi: tiếng Việt. Code, tên biến, commit message: tiếng Anh.
- Mọi con số hiển thị cho người dùng phải đi qua Stats Tools. **Không** để LLM tự tính hay tự bịa số liệu.
- Mọi output của agent đều có `evidence`. Thiếu `evidence` thì Guardrail chặn.
- Mỗi tính năng mới phải có test tối thiểu cho phần deterministic.
- Không dùng tên, logo hay dữ liệu thật của Premier League / CLB / cầu thủ.
- Nhật ký quyết định (cập nhật khi chốt):
  - _Hình thức:_ làm một mình
  - _Stack:_ Python 3.12 + uv (FastAPI) cho backend, Next.js cho UI (đã chốt 20/10)
  - _Repo / package:_ `pundit-copilot` / `pundit` (đã chốt 20/10)
  - _Roster:_ đội và cầu thủ hư cấu, mỗi đội có 1 cầu thủ Việt Nam hư cấu (đã chốt 20/10)
  - _Kiểm soát bóng:_ theo thời gian bóng lăn, cập nhật live; `pass_share` chỉ để đối chiếu (đã chốt 20/10). Chi tiết: [docs/day1-design.md](docs/day1-design.md) mục 4.4
  - _Azure subscription:_ ~~Free Account (chính) + Azure for Students (dự phòng)~~ **không có Azure** (chốt 25/10), xem mục 6
  - _Model:_ bản public: Ollama `qwen2.5:1.5b` trên CPU của Space (chờ đo lại S1 trên Space thật); ghi phiên + eval: **Foundry Local** `phi-4-mini` (chốt 25/10)
  - _Region:_ không áp dụng (không có Azure)
  - _Category chính:_ Best Multi-Agent System (đã chốt 20/10)
  - _Ngôn ngữ claim:_ tiếng Việt + tiếng Anh; câu giải thích theo ngôn ngữ của claim (đã chốt 20/10)
  - _LLM tạm khi chưa có Azure:_ ~~GitHub Models~~ (đã ngừng hoạt động 30/07/2026, phát hiện 21/10) → **Ollama chạy local**, model `qwen2.5:3b` (CPU), chung adapter OpenAI-compatible với Foundry; mock vẫn là mặc định cho dev/CI (đã chốt 21/10)
  - _Orchestrator:_ Microsoft Agent Framework Workflows, đã spike và ghim `agent-framework-core==1.22.0`, `agent-framework-openai==1.17.0`; một workflow mới cho mỗi claim; runner `asyncio` giữ làm lưới an toàn `orchestrator_fallback` (đã chốt 20/10). Chi tiết: [docs/day2-design.md](docs/day2-design.md) §9.5, §19
  - _Day 2:_ xong tầng A (mock) ngày 20/10: Claim Verifier + Guardrail + MomentDetector + API/WebSocket + CLI `claim / moments / eval / serve`. RuleAnalyst luôn đối chiếu spec của LLM; lệch thì dùng luật và gắn cờ `analyst_disagreement`. Tầng B: đã chạy với Ollama local (21/10); Foundry chờ Azure. Rà soát Day 1–2 ngày 21/10: thêm luật Guardrail `WRONG_COMPARISON` / `WRONG_ATTRIBUTION` / `FRAGMENT` / `UNGROUNDED` (mỗi câu model viết về trận phải dẫn số), oracle test 4.652 claim, CLI không còn traceback khi nhập sai (chi tiết: day2-design §19.10)
  - _Day 3 (thiết kế 22/10):_ talking point EN + VI; bình luận = moment + sự kiện chính + mốc 15' (khoảng 65–105 mục/trận, khoảng lặng ≤ 5'); template trước, LLM trong ngân sách (talking point 3 s, bình luận 2,5 s), recap và chạy offline có ngân sách dài; cá nhân hoá trên server theo từng kết nối WS (query hồ sơ, giữ chuỗi `n`); message `commentary` thay `vn_commentary`. Chi tiết: [docs/day3-design.md](docs/day3-design.md) §22
  - _Day 3 (xong 22/10):_ studio chạy trên workflow MAF fan-out / fan-in (StudioIntake → FactPackBuilder → TalkingPointWriter ‖ GraphicsDesigner ‖ Commentator → StudioPublisher), template là đường chính, LLM trong ngân sách với breaker riêng; Guardrail studio thêm `WRONG_ENTITY` / `WRONG_OUTCOME` / `WRONG_LANGUAGE` / `WRONG_REGISTER` cùng các luật rút ra khi đọc câu của model thật (tên chỉ số cạnh con số, vai trò thủ môn / người ghi bàn, từ chỉ tỷ số khớp tỷ số, phía của momentum, không chen tiếng Anh vào câu VI); cá nhân hoá theo hồ sơ trên mỗi kết nối WS (analyst / casual, VI / EN, CLB, người mới xem, theo dõi cầu thủ, một chỉ số), cùng chuỗi `n`; recap H1 / FULL hai bản; CLI `pundit studio` / `pundit recap`; `STUDIO_ENABLED=false` trả lại đúng app Ngày 2. Kết quả kiểm thử và khác biệt so với thiết kế: [docs/day3-design.md](docs/day3-design.md) §23
  - _Day 4 (thiết kế 22/10):_ Next.js 16 static export do FastAPI phục vụ (`pundit serve --ui`, một origin, một container ở Ngày 5); lower-third lên sóng tự động, biên tập viên Take / Clear / Hold qua message `on_air`; UI song ngữ (Studio / overlay mặc định EN, Fan mặc định VI); UI không tự tính số; thêm pause / resume / tốc độ không huỷ job studio, `/timeline`, `/stats`, `/events`, `/scenarios`; phạm vi P0 + P1 + màn so sánh Analyst / Casual. Chi tiết: [docs/day4-design.md](docs/day4-design.md) §17
  - _Day 4 (xong 22/10):_ frontend `frontend/` (Next.js 16.4 static export, React 19, Tailwind 4, zustand) do `pundit serve --ui` phục vụ; trang chọn trận, Studio, Overlay, Fan, So sánh; type sinh từ schema (`npm run gen:types`). Màu chữ / nền nút qua token (`--accent` / `--accent-fill`, `--*-fill` cho verdict); `dark:` bật cả trong theme Studio. Nội dung của server mang `lang` theo ngôn ngữ của nó. Kiểm thử: Vitest 73; Playwright 23 (project `e2e` trên backend thật, project `mock` phát lại fixture); axe 0 serious / critical; F7 chạy local, bỏ qua trên CI. Ngân sách JS overlay 160 KB gzip (thiết kế 120 KB thấp hơn mức sàn của React + Next). OBS 32.2.2 đã cài, profile `Pundit` 1920×1080. Chi tiết: [docs/day4-design.md](docs/day4-design.md) §18
  - _Day 5, giọng đọc (thiết kế 22/10):_ chưa có Azure nên mock-first: `TTS_PROVIDER` = `off` / `browser` / `mock` / `azure` (mặc định `mock` cho dev/CI; `browser` để nghe tiếng Việt bằng giọng của trình duyệt trước khi có Azure). Azure lỗi hoặc hết quota thì client đọc bằng giọng trình duyệt, đúng kịch bản server đã chuẩn hoá. VI trước, EN bật bằng `TTS_LANGUAGES`. Mỗi câu một clip HTTP định danh theo nội dung; message WS mang tham chiếu; chỉ tổng hợp cho hồ sơ có `voice` và message live. Mọi số đổi thành chữ bằng code, SpeechGuard kiểm tra khứ hồi từng số. Nhấn giọng bằng prosody + break (giọng vi-VN không có `emphasis` / style). Recap audio = recap + các khoảnh khắc then chốt (analyst khoảng 1–1,5 phút, casual khoảng 40–60 s), lưu kho bền local, lên Azure là Blob. Gọi Azure bằng REST, không dùng Speech SDK; Managed Identity dạng `aad#resourceId#token`. STT là P2, làm cuối. Chi tiết: [docs/day5-design.md](docs/day5-design.md) §18
  - _Day 5, giọng đọc (xong 23/10):_ tầng A (mock + giọng trình duyệt) chạy đủ. Narrator đọc mọi câu `speak` cho hồ sơ có `voice`: clip định danh theo nội dung qua `/audio`, single-flight, RateGate, breaker, ngân sách ký tự, kho bền local / Blob (đã chạy với Azurite); SpeechGuard G1–G7 chặn mọi số đọc sai. Fan VN Player có bộ lập lịch thuần (luật R1–R12, bất biến I1–I8 bằng fast-check), giọng trình duyệt khi clip lỗi, chọn nam / nữ, recap nghe / tua / nghe lại. STT (P2): `POST /speech/recognize` + nút micro trong Studio, chữ chỉ điền ô claim, chỉ hiện "đang ghi" khi micro đã có tiếng (lần dùng đầu micro mất 1,4 s mới có mẫu). Kiểm thử: backend 2040 passed (coverage 95 %), Vitest 146, Playwright 35 (Ngày 4 + A1–A11), smoke V9 hai lượt, replay 1000x có 3 người nghe chậm thêm 0,1 %. Sửa thêm lỗi có từ Ngày 4: link trận ở trang chọn trận prefetch file không tồn tại (404). Tầng B (Azure) chờ key; nghe duyệt bằng tai chờ máy có giọng tiếng Việt. Chi tiết: [docs/day5-design.md](docs/day5-design.md) §19
  - _Day 6 khi chưa có Azure (thiết kế 23/10):_ MCP server chỉ đọc qua stdio (`pundit mcp`, tự sinh trận) và `/mcp` trong app (Streamable HTTP, đọc cả phiên live), SDK `mcp` 2.3.0, `verify_claim` chỉ dùng luật; demo thật bằng GitHub Copilot Agent Mode. Eval 100 claim trên 3 trận (72 có nhãn, 28 held-out): nhãn theo số tính độc lập và được duyệt, held-out đóng băng trước khi sửa parser; kiểm toán số độc lập với Guardrail; gate CI bằng mock. Bơm lỗi bằng proxy trước Ollama (cùng client `AsyncOpenAI` với Foundry). Mỗi người xem một phiên riêng (`scenario-seed~token`), không bao giờ huỷ phiên đang có người xem. Quét secret toàn lịch sử + quét tên thật theo Rules + job CI `security`. Bug bash + hướng dẫn demo 3 bước cho giám khảo. Chi tiết: [docs/day6-design.md](docs/day6-design.md) §16
  - _Day 6 khi chưa có Azure (xong 24/10):_ MCP chỉ đọc 9 tool + 2 resource + 2 prompt qua stdio và `/mcp` (stateless, chỉ POST; đọc phiên live và phiên riêng theo mã); eval 100 claim / 3 trận (72 có nhãn, 28 held-out đóng băng ở `5a56b3c`), đếm độc lập + kiểm toán số độc lập Guardrail (thêm luật `WRONG_RATIO` / `WRONG_MINUTE`), gate CI mock ≥ 85 %: mock 96 %, 0 sai, held-out 64 %, 0 số bịa; Ollama `qwen2.5:3b` 97 %, 0 sai, held-out 75 %, 0 số bịa (lượt đầu có 2 verdict sai trên held-out: LLM đọc câu thành "áp đảo" dù câu không nói; sửa: cách đọc áp đảo chỉ LLM đưa ra phải có từ áp đảo trong câu); 3 verdict sai do held-out lộ ra được sửa như lỗi an toàn, lần đo đầu giữ trong `docs/eval.md`. Proxy bơm lỗi + ma trận C1–C13 13/13 (C8 đo model chậm với breaker đóng); p95 độ trễ dùng chung `pundit.domain.percentiles` (không ngoại suy). Phiên riêng `scenario-seed~token`: trần 16, không bao giờ huỷ phiên có người xem, 429 / 503 kèm `Retry-After`. Quét secret toàn lịch sử + tuân thủ tên thật + job CI `security` + gitleaks: sạch. Trang đầu theo ngôn ngữ trình duyệt, hướng dẫn 3 bước + địa chỉ MCP, HelpDrawer, title từng trang, trạng thái panel; demo-run S1–S7 3/3; link mã nguồn / video lấy từ `NEXT_PUBLIC_REPO_URL` / `NEXT_PUBLIC_VIDEO_URL` lúc build. Còn chờ bạn: M1 Copilot (điền `docs/copilot.md`), M4 duyệt nhãn, M5–M10. Chi tiết: [docs/day6-design.md](docs/day6-design.md) §17
  - _Day 6, phần bổ sung (24/10 chiều):_ M3 bằng Inspector CLI (`as_of` nhận số nguyên); M5 tôi đọc trước 20 câu: 2 câu sai nghĩa đã lên màn hình (c16, c17) → luật `WRONG_ATTRIBUTION` cho cặp giá trị sau đội duy nhất được nhắc và `WRONG_COMPARISON` cho số sau "hơn / than"; `pundit studio --audit` (mock 0 / 2938 số không nguồn; Ollama 1 / 545: "0.4" cho 0.44) → Guardrail từ chối số viết thô hơn fact trên màn hình, oracle studio coi "1.5" = "1.50"; bug bash điện thoại giả lập + Studio 1x 10 phút: PASS; M6 tắt / bật Ollama thật: fallback, breaker, chip, UI chạy tiếp; lần thử đầu sau khi bật lại hết giờ ở mặc định 90 s (lời gọi analyst đầu sau khi nạp model mất 80–117 s khi studio chen vào), `LLM_TIMEOUT_S=180` thì 10/10; M10: bộ ảnh nay giống app thật (tên cầu thủ, timeline / stats ghi từ backend, stream `analyst-vi`, fixture A làm mới, hiệu ứng về trạng thái cuối để axe không bắt lúc thẻ đang mờ), và lộ một lỗi thật có từ Ngày 4: chi tiết trong feed sự kiện là giá trị thô tiếng Anh ("blocked", "off_target") cả ở UI tiếng Việt, nay dịch theo ngôn ngữ trang; Docker một image (542 MB, không root) + job CI `image`; Bicep / `azd` / `deploy.yml` viết xong, biên dịch sạch, **chưa deploy**; App Insights chưa gửi log / trace từ code; nháp README / pitch / kịch bản video + phụ đề. Chi tiết: [docs/day6-design.md](docs/day6-design.md) §17.7
  - _Day 7 (thiết kế lại 25/10): **chắc chắn không có Azure**._ Demo public: một image trên Hugging Face Space (CPU basic miễn phí), build bằng GitHub Actions → GHCR → Space. Model nhỏ trong container (Ollama, model chốt bằng spike S1, mặc định `qwen2.5:1.5b`) chỉ cho claim; claim **progressive** (rev.1 theo luật < 1 s, rev.2 do model). Studio trên Space phát lại phiên ghi bằng **Foundry Local** (`phi-4-mini`, nhãn "recorded"), thiếu thì template. Thêm trang **Agent Ops** (`agent_event`), **Chaos** theo phiên riêng (có "model nói dối" để Guardrail chặn). Giọng Việt: **Piper** trong Space nếu qua spike S2 (license), dự phòng giọng trình duyệt. Keepalive 3 lớp: tự ping trong app + watchdog Actions (bạn viết bằng Copilot Agent Mode, tự restart Space bằng HF token) + UptimeRobot. Voice-over: Clipchamp TTS. Bicep / `azd` giữ lại, ghi "Azure-ready, not deployed". Chi tiết: [docs/day7-design.md](docs/day7-design.md)
  - _Day 7, C1–C3 (xong 25/10):_ repo `Hoangnam25012004/epl_football_hackathon` (push bằng Git Credential Manager; CI xanh sau khi sửa 2 test chạy lúc qua lúc hỏng: overlay chụp giữa animation, RateGate giả định thứ tự luồng). Image Space `deploy/space/` (Ollama v0.40.2 bản CPU + `qwen2.5:1.5b`, uid 1000, 2,58 GB, `/healthz` sau 26 s); app thêm `LLM_LIVE_SCOPE=claims`, keepalive lớp 1 (`KEEPALIVE_URL`), `build` trong `/healthz`; `space.yml` (sau `ci` xanh: build → GHCR → Space theo digest → smoke) + `tools/ops/`. **S1 không đạt ở máy dev**: p95 160 s (ngưỡng 45 s), 0 verdict sai; sửa lỗi llama.cpp chạy 8 luồng trên 2 CPU (`num_thread` theo cgroup). Claim trên Space tạm thời tối đa 45 s cho tới khi có C4. Lần deploy đầu: Space **PAUSED** vì tài khoản Hugging Face mới có hạn mức cpu-basic = 0, chờ nâng PRO. Chi tiết: [docs/day7-design.md](docs/day7-design.md) §7
  - _Day 7, C4–C11 (26/10):_ claim **progressive** (`CLAIM_PROGRESSIVE`: rev.1 = workflow không model, rev.2 = workflow một lượt trên một worker, cùng message `claim` mang `revision`; store không cho bản cũ đè bản mới); **Agent Ops** (listener trên `TraceRecorder`, WebSocket riêng `/ws/agents/{id}` để không đụng bộ đếm `n` của trận, lô 100 ms không bao giờ bỏ sự kiện claim, trang `/agents`); **Chaos** theo phiên riêng (6 lỗi, service + breaker riêng, "model nói dối" bị Guardrail chặn hết, câu sai nhớ kèm claim / khoảnh khắc); C9 không còn chữ "mock" trên UI; C11 pitch / kịch bản / voice-over / phụ đề không Azure; LICENSE MIT. Spike: `qwen2.5:0.5b` và Piper (RTF 0,77 trên 1 CPU) đều **không đạt gate** → giữ 1.5b, giữ giọng trình duyệt, C8 không làm. Chi tiết: [docs/day7-design.md](docs/day7-design.md) §7.3
  - _Day 7, M2b / C10 / M3 / M10 (27/10):_ Hugging Face PRO, package GHCR public, Space **RUNNING**, `space.yml` tự deploy sau `ci` xanh. Eval 100 claim trên Space: 96 %, 0 verdict sai, 1 số bịa ("2-0" sau từ chỉ kết quả → luật `WRONG_OUTCOME`); Foundry Local 0.12.1 `Phi-4-mini-instruct-generic-cpu:5` (bản CPU), 20 claim 100 %, 0 số bịa. **Gate S1 trên Space không đạt** (lượt model p95 54,8 s > 45 s): giữ `qwen2.5:1.5b` vì claim là progressive (rev.1 p95 0,5 s với 1 người xem, 1,2 s với 3 người xem). Lỗi lộ ra trên Space: janitor dọn phiên chung đang dùng qua API (`hub.touch()`), lượt model bị Guardrail chặn bị báo "không kịp trả lời" (`skip_reason: rejected`), hàng đợi talking point trống vi phạm axe `list`. Bug bash tự động trên Space: `frontend/playwright.space.config.ts` (Edge / Chrome / điện thoại, chạy tay). Chi tiết: [docs/day7-design.md](docs/day7-design.md) §7.4, [docs/eval.md](docs/eval.md)
  - _Day 7, M5 (27/10):_ UptimeRobot (keepalive lớp 3) xong và đã kiểm chứng thật: monitor Keyword trên `/healthz`, keyword `"status":"ok"`, báo khi không tồn tại, 5 phút, email; thử báo động bằng keyword sai → Down 09:18:12 UTC, email DOWN, đổi lại → Up 09:19:18 UTC, email UP. Monitor HTTP cũ đã xoá. Uptime 24 giờ hiển thị 89,1 % chỉ vì lần thử này. Chi tiết: [docs/day7-design.md](docs/day7-design.md) §7.5
  - _Day 7, M10 iPhone + C14 (28/10):_ nghe thật trên iOS: giọng Việt đúng số liệu nhưng nam giống nữ, đều và chậm, chỉ đọc tình huống lớn. Sửa: **mọi câu bình luận đều được đọc** (`speak()` trước đây bỏ cú sút / phạt góc / việt vị / VAR); cue nhỏ mới `tackle` / `foul` / `box_entry` (cách câu trước ≥ 40 s, có số đếm của đội); mốc thống kê mỗi 5' (lower-third vẫn chỉ 30' / 75'); khoảng lặng thường ≤ 2', trần 3' (110–154 câu / trận, trước 53–99); preset giọng nhanh hơn (`calm` +8 %, `peak` +25 %, `burst` +30 %), cú sút bị cứu thua đọc lively; nút nam / nữ chỉ hiện khi hai giọng thực sự khác nhau (iPhone chỉ có một giọng vi-VN). Video quay trang Fan bằng Edge desktop (giọng HoaiMy / NamMinh Online Natural). Máy dev chặn mọi `python.exe` từ 28/10: test backend chạy trong image `pundit-copilot` có mount mã nguồn. Chi tiết: [docs/day7-design.md](docs/day7-design.md) §7.6
  - _Day 7, C12 + nộp bài (28/10):_ watchdog lớp 2 (`tools/ops/watchdog.py`, `watchdog.yml` mỗi 20', 17 test) do Claude viết vì M4 quá hạn, không ghi cho Copilot; README hoàn chỉnh (thử 3 bước, bảng 5 tiêu chí, keepalive); `docs/submission.md` có mọi ô của form, chỉ thiếu link video. C7 không làm. Chi tiết: [docs/day7-design.md](docs/day7-design.md) §7.7
  - _Azure:_ Free Account bằng email trường bị từ chối ("not eligible", 20/10); **chốt 25/10 không dùng Azure**. Adapter Foundry cloud / Azure Speech / Blob và Bicep / `azd` giữ trong repo, ghi "Azure-ready, not deployed"
