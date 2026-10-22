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
- **Bình luận tiếng Việt real-time:** chuyển sự kiện thành câu bình luận tiếng Việt tự nhiên, rồi đọc bằng **Azure AI Speech** (giọng neural `vi-VN-HoaiMyNeural` / `vi-VN-NamMinhNeural`).
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

### Các agent

| Agent | Vai trò | Dùng LLM? |
|---|---|---|
| Moment Detector | Phát hiện khoảnh khắc quan trọng (bàn thắng, đổi momentum, chuỗi nguy hiểm) | Không (rule-based) |
| Claim Verifier | Tách claim thành truy vấn chỉ số, gọi Stats Tools, ra verdict | Có (phân tích claim) + tool |
| Talking-Point | Sinh câu bình luận ngắn kèm số liệu | Có |
| Graphics | Chọn chỉ số và template lower-third, xuất JSON | Có (nhẹ) |
| Vietnamese Commentator | Viết bình luận / recap tiếng Việt, chế độ "Người mới xem" | Có |
| Personalization | Điều chỉnh output theo hồ sơ người xem (Analyst / Casual / CLB / cầu thủ / ngôn ngữ) | Có |
| Guardrail / Verifier | (1) Mọi con số trong output phải khớp với Stats Tools, sai thì bác bỏ. (2) Kiểm tra nội dung an toàn: không xúc phạm, phân biệt đối xử hay hạ thấp cầu thủ / CLB / trọng tài, theo Official Rules §4.4 và Microsoft Event Code of Conduct. Dùng bộ lọc content safety có sẵn trên Foundry | Không (deterministic) + content filter + LLM-judge tuỳ chọn |

**Yêu cầu cho hạng mục Multi-Agent:** các agent đọc và ghi qua một **shared match state**, handoff có cấu trúc (typed message), có retry / fallback khi một agent lỗi, và hiển thị toàn bộ trace trên UI.

**MCP server:** đóng gói Stats Tools thành một MCP server (read-only) để agent và client AI bên ngoài (VD GitHub Copilot, Claude, hệ thống của đài) dùng chung. Server này đáp ứng ý "MCP integration" trong tiêu chí Agentic.

## 4. Tech stack đề xuất

> Mặc định bên dưới; đổi nếu đội mạnh về stack khác. Ghi lại quyết định vào mục 8.

- **Backend / Agents:** Python 3.12 (quản lý bằng `uv`), FastAPI, Microsoft Agent Framework (hoặc Semantic Kernel), SDK của Microsoft Foundry.
- **Frontend:** Next.js (TypeScript) + Tailwind; overlay lower-third là trang HTML riêng có nền trong suốt để đưa vào OBS / vMix.
- **Real-time:** WebSocket (FastAPI) hoặc Azure Web PubSub / SignalR.
- **Data contracts:** Pydantic models + JSON Schema dùng chung.
- **Test / CI:** pytest, GitHub Actions.
- **IaC:** Bicep (hoặc `azd` template).

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
- [ ] Orchestrator trên Microsoft Agent Framework, kết nối model trên **Microsoft Foundry** (dùng thật, không chỉ có mock).
- [ ] Claim Verifier: chuyển claim sang tool call, ra verdict có lý do.
- [ ] Talking-Point Agent + Graphics Agent.
- [ ] Guardrail kiểm tra số liệu và bác bỏ output sai.
- [ ] Ghi **Agent Trace** (agent nào gọi tool nào, kết quả ra sao) để hiển thị trên UI, phục vụ tiêu chí "explainable".

### 5.4. Tiếng Việt & giọng đọc (P0 cho text, P1 cho voice)
- [ ] Vietnamese Commentator Agent với system prompt văn phong bình luận Việt và bảng thuật ngữ.
- [ ] Chế độ "Người mới xem": thư viện giải thích luật ngắn gọn, tự kích hoạt khi tình huống liên quan xảy ra.
- [ ] Azure AI Speech TTS với SSML (nhấn giọng khi có bàn thắng, tốc độ đọc thay đổi theo mức kịch tính).
- [ ] Recap cuối hiệp / cuối trận gồm văn bản + file audio.
- [ ] (P2) Speech-to-Text để nhận claim bằng giọng nói.

### 5.5. Giao diện (P0)
- [ ] Studio Dashboard: feed sự kiện, scoreboard, timeline momentum, ô nhập claim, hàng đợi talking points (duyệt / bỏ / ghim), drawer "Why / Evidence / Agent Trace".
- [ ] Trang overlay lower-third (nền trong suốt, animation vào ra).
- [ ] Fan VN Player: chọn trận, nghe bình luận tiếng Việt, bật / tắt chế độ "Người mới xem".
- [ ] Nhãn "Synthetic data" trên mọi màn hình.

### 5.6. Hạ tầng & triển khai (P0)
- [ ] Dockerfile cho backend và frontend.
- [ ] Bicep / `azd` để dựng toàn bộ resource (mục 6).
- [ ] Deploy lên **Azure Container Apps** với URL public để giám khảo dùng thử.
- [ ] Health endpoint, logging và tracing qua Application Insights.
- [ ] GitHub Actions: test → build image → deploy.

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

### 6.0. Azure subscription: dùng 2 subscription (chính + dự phòng)

| Vai trò | Loại | Credit | Thời hạn | Dùng cho |
|---|---|---|---|---|
| **Chính** | **Azure Free Account** (xác minh bằng thẻ Visa) | 200 USD | 30 ngày (tạo 20–21/10 → hết khoảng 19–20/11, phủ hết thời gian chấm 10/11) | Toàn bộ môi trường demo: Foundry, Speech, Container Apps… |
| **Dự phòng** | **Azure for Students** (email trường, không cần thẻ) | 100 USD | 12 tháng | Thay thế nếu subscription chính bị giới hạn quota model, hoặc giữ demo sống sau khi Free Account hết hạn |

- Hai offer này độc lập nhau. Dùng chung một tài khoản Microsoft vẫn được, chúng hiện thành 2 subscription riêng.
- Free Account **không tự trừ tiền thẻ** khi hết credit, trừ khi chủ động nâng cấp lên Pay-As-You-Go. **Không nâng cấp** nếu chưa thật sự cần.
- **Việc đầu tiên sau khi tạo:** deploy thử một model trên Microsoft Foundry ở **cả hai** subscription. Subscription nào deploy được model với quota đủ dùng thì làm subscription chính. Ghi kết quả vào mục 8.
- Hạ tầng dựng bằng IaC (Bicep / `azd`), nên chuyển sang subscription khác chỉ cần chạy lại `azd up`.
- Đặt budget alert ở mức 50% và 80% credit trên subscription chính.

### 6.1. Azure resources

| Resource | Mục đích | SKU / ghi chú |
|---|---|---|
| Resource Group | Gom toàn bộ tài nguyên | 1 RG, VD `rg-pundit-copilot` |
| **Microsoft Foundry** (project + model deployment) | LLM cho các agent | Một model "mini" độ trễ thấp cho agent real-time + một model mạnh hơn cho recap (tuỳ chọn). Chọn region có sẵn model (VD East US 2 / Sweden Central) |
| **Azure AI Speech** | TTS tiếng Việt (và STT tuỳ chọn) | S0 (F0 free đủ cho dev nhưng giới hạn quota). Region: Southeast Asia để giảm độ trễ |
| **Azure Container Apps** + Environment | Chạy API, agent worker, frontend | Consumption plan, min replicas = 1 trong thời gian chấm để tránh cold start |
| Azure Container Registry | Lưu Docker image | Basic |
| Azure Web PubSub *(tuỳ chọn)* | Đẩy real-time tới nhiều client | Free tier đủ cho demo; nếu không dùng thì dùng WebSocket trực tiếp |
| Azure Blob Storage | Lưu file audio recap, dữ liệu trận mẫu | Standard LRS |
| Azure Cosmos DB *(tuỳ chọn)* hoặc Redis | Lưu match state / lịch sử claim | Bản demo có thể giữ in-memory; Cosmos serverless nếu cần bền vững |
| Application Insights + Log Analytics | Logging, tracing agent, đo độ trễ | Pay-as-you-go |
| Azure Key Vault | Lưu key / secret | Standard; ưu tiên **Managed Identity** thay cho API key |

> Cần thêm: **Azure Event Hubs** chỉ khi muốn nhấn mạnh hạng mục Cloud Native. Ở quy mô demo, event bus in-process là đủ.

### 6.2. Yêu cầu phi chức năng
- **Độ trễ:** event tới talking point < 3s; event tới audio tiếng Việt < 5s.
- **Độ tin cậy:** khi Foundry lỗi hoặc timeout, fallback sang template deterministic, UI vẫn chạy (không được crash khi giám khảo đang thử).
- **Bảo mật:** không commit key; dùng Managed Identity / Key Vault; `.env.example` chỉ chứa tên biến.
- **Tái lập:** `azd up` (hoặc một script) dựng được toàn bộ môi trường; trận mẫu có seed cố định.
- **Chi phí:** dự kiến < 50 USD cho cả thời gian hackathon. Đặt **budget alert** trên subscription, và tắt hoặc scale về 0 sau khi chấm xong.

### 6.3. Môi trường phát triển local
- Python 3.12 + `uv`, Node.js 20+, Docker Desktop, Azure CLI + `azd`, Git.
- Chạy được offline bằng **mock LLM** (để dev và test không tốn quota), bật Foundry thật bằng biến môi trường.
- Biến môi trường chính (tên dự kiến): `FOUNDRY_ENDPOINT`, `FOUNDRY_MODEL_DEPLOYMENT`, `SPEECH_REGION`, `SPEECH_RESOURCE_ID` (hoặc key khi dev), `APPLICATIONINSIGHTS_CONNECTION_STRING`, `USE_MOCK_LLM`.

## 7. Lịch 7 ngày (20/10 → 27/10/2026)

| Ngày | Mục tiêu |
|---|---|
| 21/10 | Schema + Synthetic Match Engine + Stats Tools + test |
| 22/10 | Orchestrator + Claim Verifier + Guardrail trên Foundry |
| 23/10 | Talking-Point + Graphics + Vietnamese Commentator |
| 24/10 | Studio Dashboard + overlay lower-third + Fan VN Player |
| 25/10 | Azure Speech TTS + deploy lên Container Apps + App Insights |
| 26/10 | Eval, tối ưu độ trễ, sửa lỗi, đánh bóng UI |
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
  - _Azure subscription:_ Free Account (chính) + Azure for Students (dự phòng), xem mục 6.0 (đã chốt 20/10)
  - _Model Foundry:_ chưa chốt
  - _Region:_ chưa chốt
  - _Category chính:_ Best Multi-Agent System (đã chốt 20/10)
  - _Ngôn ngữ claim:_ tiếng Việt + tiếng Anh; câu giải thích theo ngôn ngữ của claim (đã chốt 20/10)
  - _LLM tạm khi chưa có Azure:_ ~~GitHub Models~~ (đã ngừng hoạt động 30/07/2026, phát hiện 21/10) → **Ollama chạy local**, model `qwen2.5:3b` (CPU), chung adapter OpenAI-compatible với Foundry; mock vẫn là mặc định cho dev/CI (đã chốt 21/10)
  - _Orchestrator:_ Microsoft Agent Framework Workflows, đã spike và ghim `agent-framework-core==1.22.0`, `agent-framework-openai==1.17.0`; một workflow mới cho mỗi claim; runner `asyncio` giữ làm lưới an toàn `orchestrator_fallback` (đã chốt 20/10). Chi tiết: [docs/day2-design.md](docs/day2-design.md) §9.5, §19
  - _Day 2:_ xong tầng A (mock) ngày 20/10: Claim Verifier + Guardrail + MomentDetector + API/WebSocket + CLI `claim / moments / eval / serve`. RuleAnalyst luôn đối chiếu spec của LLM; lệch thì dùng luật và gắn cờ `analyst_disagreement`. Tầng B: đã chạy với Ollama local (21/10); Foundry chờ Azure. Rà soát Day 1–2 ngày 21/10: thêm luật Guardrail `WRONG_COMPARISON` / `WRONG_ATTRIBUTION` / `FRAGMENT` / `UNGROUNDED` (mỗi câu model viết về trận phải dẫn số), oracle test 4.652 claim, CLI không còn traceback khi nhập sai (chi tiết: day2-design §19.10)
  - _Day 3 (thiết kế 22/10):_ talking point EN + VI; bình luận = moment + sự kiện chính + mốc 15' (khoảng 65–105 mục/trận, khoảng lặng ≤ 5'); template trước, LLM trong ngân sách (talking point 3 s, bình luận 2,5 s), recap và chạy offline có ngân sách dài; cá nhân hoá trên server theo từng kết nối WS (query hồ sơ, giữ chuỗi `n`); message `commentary` thay `vn_commentary`. Chi tiết: [docs/day3-design.md](docs/day3-design.md) §22
  - _Day 3 (xong 22/10):_ studio chạy trên workflow MAF fan-out / fan-in (StudioIntake → FactPackBuilder → TalkingPointWriter ‖ GraphicsDesigner ‖ Commentator → StudioPublisher), template là đường chính, LLM trong ngân sách với breaker riêng; Guardrail studio thêm `WRONG_ENTITY` / `WRONG_OUTCOME` / `WRONG_LANGUAGE` / `WRONG_REGISTER` cùng các luật rút ra khi đọc câu của model thật (tên chỉ số cạnh con số, vai trò thủ môn / người ghi bàn, từ chỉ tỷ số khớp tỷ số, phía của momentum, không chen tiếng Anh vào câu VI); cá nhân hoá theo hồ sơ trên mỗi kết nối WS (analyst / casual, VI / EN, CLB, người mới xem, theo dõi cầu thủ, một chỉ số), cùng chuỗi `n`; recap H1 / FULL hai bản; CLI `pundit studio` / `pundit recap`; `STUDIO_ENABLED=false` trả lại đúng app Ngày 2. Kết quả kiểm thử và khác biệt so với thiết kế: [docs/day3-design.md](docs/day3-design.md) §23
  - _Day 4 (thiết kế 22/10):_ Next.js 16 static export do FastAPI phục vụ (`pundit serve --ui`, một origin, một container ở Ngày 5); lower-third lên sóng tự động, biên tập viên Take / Clear / Hold qua message `on_air`; UI song ngữ (Studio / overlay mặc định EN, Fan mặc định VI); UI không tự tính số; thêm pause / resume / tốc độ không huỷ job studio, `/timeline`, `/stats`, `/events`, `/scenarios`; phạm vi P0 + P1 + màn so sánh Analyst / Casual. Chi tiết: [docs/day4-design.md](docs/day4-design.md) §17
  - _Day 4 (xong 22/10):_ frontend `frontend/` (Next.js 16.4 static export, React 19, Tailwind 4, zustand) do `pundit serve --ui` phục vụ; trang chọn trận, Studio, Overlay, Fan, So sánh; type sinh từ schema (`npm run gen:types`). Màu chữ / nền nút qua token (`--accent` / `--accent-fill`, `--*-fill` cho verdict); `dark:` bật cả trong theme Studio. Nội dung của server mang `lang` theo ngôn ngữ của nó. Kiểm thử: Vitest 73; Playwright 23 (project `e2e` trên backend thật, project `mock` phát lại fixture); axe 0 serious / critical; F7 chạy local, bỏ qua trên CI. Ngân sách JS overlay 160 KB gzip (thiết kế 120 KB thấp hơn mức sàn của React + Next). OBS 32.2.2 đã cài, profile `Pundit` 1920×1080. Chi tiết: [docs/day4-design.md](docs/day4-design.md) §18
  - _Azure:_ Free Account bằng email trường bị từ chối ("not eligible", 20/10); đang thử tài khoản Microsoft cá nhân / Azure for Students
