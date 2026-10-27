# Ngày 7: hoàn thiện và nộp bài khi không có Azure

> Thiết kế ngày 25/10/2026 (thay bản cùng ngày có hai nhánh Azure / không Azure). Dữ liệu tổng hợp: đội, cầu thủ và trận đấu đều hư cấu.
> **Đã chốt: không có Azure.** Demo public chạy trên Hugging Face Space (CPU miễn phí). Tài liệu chia hai phần: §2 là việc Claude làm (thiết kế + tiêu chí đạt), §3 là việc bạn làm bằng tay (hướng dẫn từng bước).
> Tài liệu trước: [day5-design.md](day5-design.md) §19, [day6-design.md](day6-design.md) §15, §17.

---

## 0. Quyết định, mục tiêu, mốc giờ

### 0.1. Quyết định đã chốt (25/10)

| # | Chủ đề | Chốt |
|---|---|---|
| 1 | Hạ tầng | Không Azure. Một image Docker trên **Hugging Face Space** (CPU basic: 2 vCPU, 16 GB, miễn phí). Bicep / `azd` giữ trong repo với ghi chú "Azure-ready, not deployed" |
| 2 | LLM trên Space | **Model nhỏ chạy ngay trong container**: Ollama + `qwen2.5:1.5b` (Apache-2.0) trên CPU, chỉ cho luồng claim. Model chốt sau spike S1 (§2.1) |
| 3 | Trải nghiệm claim | **Progressive**: verdict theo luật + số liệu hiện ngay (< 1 s); ClaimAnalyst / Explainer chạy model sau đó, có tiến trình live, xong thì cập nhật lời giải thích và kết quả đối chiếu |
| 4 | Studio trên Space | Không gọi model live (ngân sách 2,5–3 s không kịp trên CPU). Câu do model viết lấy từ **phiên đã ghi** bằng Foundry Local (gắn nhãn "recorded"); thiếu bản ghi thì dùng template |
| 5 | Tính năng bù điểm | **Trang Agent Ops** (multi-agent live), **công tắc Chaos** cho giám khảo, **Foundry Local** cho eval và để ghi phiên, **phát lại phiên model đã ghi** |
| 6 | Giọng tiếng Việt | **Piper TTS** (neural, mã nguồn mở) chạy CPU trong Space; giọng trình duyệt giữ làm dự phòng. Điều kiện: license của engine và giọng đạt (S2) |
| 7 | Giữ Space thức | Ba lớp độc lập: **tự ping trong container** + **watchdog GitHub Actions** (tự restart Space bằng HF token) + **UptimeRobot**. Chạy từ lúc deploy, không cần ai vào xem |
| 8 | Copilot Agent Mode (dev) | **Bạn dùng Copilot viết watchdog** (lớp 2). Lý do ở §3.5 |
| 9 | Voice-over video | **Clipchamp text-to-speech** (giọng neural của Microsoft) |
| 10 | Hạng mục dự thi | Grand Prize + **Best Multi-Agent System** (không đổi) |

### 0.2. Kiến trúc đích

```
GitHub repo ──CI (test, security, eval gate)──► GitHub Actions `space.yml`
                                                  │ build image app + Ollama + model + Piper + clip đã warm
                                                  ▼
                                          GHCR (public image, ghim digest)
                                                  │ cập nhật Space qua huggingface_hub
                                                  ▼
┌──────────────── Hugging Face Space (1 container, CPU 2 vCPU) ────────────────┐
│ start.sh: ollama serve (127.0.0.1, model nạp sẵn, keep-alive) → pundit serve   │
│ FastAPI + UI tĩnh + /mcp + WebSocket                                           │
│   Claim workflow (MAF): Intake → RuleAnalyst → Evidence → Judge → Template     │
│     → Guardrail → publish rev.1  ⟶  model pass: ClaimAnalyst(LLM) → đối chiếu   │
│     → Evidence → Judge → Explainer(LLM) → Guardrail → publish rev.2            │
│   Studio workflow (MAF): Moment → FactPack → TP ‖ Graphics ‖ Commentator        │
│     (bản ghi Foundry Local hoặc template) → Guardrail → Publisher → Narrator    │
│   Narrator → Piper (vi-VN) → clip /audio                                       │
│   agent_event stream → trang Agent Ops · Chaos (chỉ phiên riêng)               │
│   keepalive: tự ping URL public mỗi 20'                                        │
└───────────────────────────────────────────────────────────────────────────────┘
        ▲ /healthz mỗi 20'                         ▲ /healthz mỗi 5'
GitHub Actions `watchdog.yml` (restart, issue)     UptimeRobot (email khi sập)
```

### 0.3. Tiêu chí chấm → hạng mục

| Tiêu chí | Mất gì khi không có Azure | Bù bằng |
|---|---|---|
| **Agentic Design & Innovation** | Không có Foundry cloud | Model thật chạy live trong Space (claim, progressive); phiên Foundry Local được ghi lại rồi phát lại qua đúng pipeline + Guardrail; **Agent Ops** cho thấy handoff có kiểu, shared state `as_of`, breaker; MCP public cho Copilot; Chaos cho thấy phục hồi |
| **User Experience & Presentation** | Không có giọng Azure | Piper: thiết bị nào cũng nghe được tiếng Việt; claim trả lời < 1 s rồi tinh chỉnh; Agent Ops dễ đọc; Space không ngủ nên giám khảo không phải chờ; nhãn trạng thái bằng lời thường thay cho "mock" |
| **Adherence to Category** (Best Multi-Agent System) | – | Vai trò riêng (≥ 11 agent), shared state, handoff có kiểu, phục hồi khi lỗi: giám khảo **tự gây lỗi** và xem hệ thống phục hồi; agent đối chiếu nhau (ClaimAnalyst vs RuleAnalyst, Guardrail bác bỏ model "nói dối") |
| **Real-World Impact** (phần lớn) | Chưa chứng minh quy mô cloud | Toàn bộ stack chạy trong **một container CPU với model mở**: đài truyền hình chạy on-prem / xe OB, dữ liệu không rời máy, chi phí bằng 0; MCP cắm vào hệ thống newsroom; mẫu "trả lời ngay, tinh chỉnh sau" đúng kiểu phát sóng; số đo độ trễ thật trên phần cứng yếu |
| **Technological Implementation** | – | Không đổi (engine tổng hợp, eval, Guardrail, CI, chaos) + pipeline GitHub → GHCR → Space tự động, watchdog |
| Vòng 1 (hero technologies) | Không có Foundry cloud / Azure services | **Microsoft Agent Framework** (lõi điều phối), **Microsoft Foundry Local** (ghi phiên + eval), **GitHub Copilot Agent Mode** (client MCP + viết watchdog), **GitHub CLI / Actions / GHCR**, **MCP**. README ghi đúng sự thật |

### 0.4. Mốc giờ (giờ Việt Nam)

| Mốc | Khi |
|---|---|
| Hạn chính thức | 27/10 23:59 PT = **28/10 13:59** |
| Mục tiêu nộp | **27/10 22:00** |
| **F1: Space chạy, ba lớp keepalive bật** | **26/10 14:00** |
| **F2: đóng băng tính năng** (sau mốc này chỉ sửa lỗi) | **26/10 23:00** |
| **F3: bắt đầu quay** | **27/10 09:00** |
| Demo phải chạy | tới hết **10/11** |

### 0.5. Bảng phân công

**Claude làm** (thiết kế ở §2). Ưu tiên P0 bắt buộc, P1 cắt được theo thứ tự ở §4.

| ID | Việc | Ưu tiên | Cần trước | Xong trước |
|---|---|---|---|---|
| C1 | Spike S1 (model trên CPU 2 vCPU) + S2 (Piper: license, chất lượng, tốc độ) | P0 | – | 26/10 09:00 |
| C2 | Image cho Space: Ollama + model + Piper + clip warm + `start.sh` + tự ping (lớp 1) | P0 | C1 | F1 |
| C3 | Workflow `space.yml`: build → GHCR → cập nhật Space → smoke | P0 | C2, M1, M2 | F1 |
| C4 | Claim progressive | P0 | – | 26/10 18:00 |
| C5 | Luồng `agent_event` + trang Agent Ops | P0 | – | 26/10 20:00 |
| C6 | Công tắc Chaos theo phiên | P1 | C5 | F2 |
| C7 | Ghi / phát lại phiên model (Foundry Local) | P1 | M3 | F2 (ghi chạy qua đêm) |
| C8 | Piper TTS provider | P0 | C1 (S2) | 26/10 16:00 |
| C9 | Đánh bóng UX cho bản public | P0 | C4, C5, C8 | F2 |
| C10 | Eval (Space model, Foundry Local) + đo độ trễ trên Space | P0 | C3, M3 | 27/10 12:00 |
| C11 | Tài liệu nộp bài, kịch bản video, lời voice-over | P0 | mọi việc trên | 27/10 18:00 |
| C12 | Review watchdog do Copilot viết (viết thay nếu quá hạn) | P0 | M4 | 26/10 20:00 |
| C13 | Ghi kết quả duyệt nhãn vào eval, chạy gate | P0 | M6 | 27/10 12:00 |

**Bạn làm** (hướng dẫn ở §3):

| ID | Việc | Thời lượng | Xong trước |
|---|---|---|---|
| M0 | Cài `uv`, `gh` | 15' | 25/10 tối |
| M1 | GitHub repo: tạo, push, CI, quyền Actions, đặt public | 30' | 26/10 10:00 |
| M2 | Hugging Face: tài khoản, Space trống, token, secret / variable trên GitHub | 30' | 26/10 10:00 |
| M3 | Cài Foundry Local + model | 30–60' | 26/10 12:00 |
| M4 | Watchdog bằng GitHub Copilot Agent Mode (lớp 2) | 90' | 26/10 20:00 |
| M5 | UptimeRobot (lớp 3) | 15' | ngay sau F1 |
| M6 | Duyệt bảng nhãn eval | 60–90' | 27/10 10:00 |
| M7 | Xác nhận 20 câu giải thích Ollama | 20' | 27/10 10:00 |
| M8 | Copilot Agent Mode hỏi trận qua MCP (local + Space) | 60' | 27/10 10:00 |
| M9 | Nghe duyệt giọng Piper | 45' | 26/10 22:00 |
| M10 | Bug bash trên Space (máy khác, điện thoại, trình duyệt khác) | 60' | 27/10 09:00 |
| M11 | Voice-over bằng Clipchamp | 30' | 27/10 10:00 |
| M12 | Quay video | 2–3 h | 27/10 13:00 |
| M13 | Dựng video + phụ đề | 2 h | 27/10 16:00 |
| M14 | Upload YouTube | 30' | 27/10 17:00 |
| M15 | Kiểm tra cuối, tag bản nộp | 45' | 27/10 21:00 |
| M16 | Nộp trên Innovation Studio | 30' | **27/10 22:00** |
| M17 | Vận hành tới 10/11 | 10'/ngày | 10/11 |

---

## 1. Nguyên tắc (bổ sung Ngày 1–6)

1. **Không nói sai về chính mình.** Mọi chữ trên UI, README, video nói đúng nguồn của câu chữ: model live (tên model, CPU), model đã ghi (tên model, Foundry Local, ngày ghi), luật, template. Không dùng từ "mock" trên bản public.
2. **Guardrail không có ngoại lệ.** Câu từ model live, model đã ghi hay template đều đi qua cùng Guardrail. Phát lại bản ghi chạy lại toàn bộ pipeline, không phát lại output đã duyệt.
3. **Nhanh trước, đúng luôn.** Câu trả lời đầu tiên của claim đến từ luật (< 1 s). Model chỉ được thêm lời giải thích hoặc đọc được claim mà luật không đọc được; nếu model đọc khác luật thì luật thắng (giữ quy tắc Ngày 2).
4. **Chaos chỉ trong phiên riêng.** Lỗi do giám khảo bật không bao giờ chạm tới phiên khác, breaker của phiên khác hay trận dùng chung.
5. **Không có người xem thì vẫn thức.** Space phải chạy và trả lời ngay cả khi không ai mở nó trong nhiều ngày.
6. Mọi thay đổi có test cho phần tất định, theo quy ước của repo.

---

## 2. Hạng mục Claude làm

### 2.1. C1: spike (gate cho C2, C8)

**S1, model trên CPU.** Chạy image thử bằng `docker run --cpus 2 --memory 16g` (giả lập phần cứng Space), rồi xác nhận lại trên Space thật sau C3.

| Ứng viên | License | Ghi chú |
|---|---|---|
| `qwen2.5:1.5b` (Q4) | Apache-2.0 | Mặc định |
| `qwen2.5:0.5b` | Apache-2.0 | Nếu 1.5B quá chậm |
| `phi4-mini` (3.8B) | MIT | Model của Microsoft; dùng nếu đủ nhanh |

Đo trên 20 claim của bộ eval: thời gian ClaimAnalyst, Explainer, cả model pass (p50 / p95, lượt đầu và lượt có cache prefix), RAM, CPU khi Piper chạy cùng lúc. Đo đúng kết quả: verdict sai, số không nguồn.

**Gate S1:** model pass p95 ≤ 45 s, **0 verdict sai**, 0 số không nguồn trên 20 claim. Ưu tiên model lớn nhất qua gate. Không model nào qua: Space chỉ dùng luật + bản ghi Foundry Local (C7 thành P0), ghi rõ trong README.

**S2, Piper.** Kiểm tra:
- license của engine (bản nào; bản mới trên PyPI có thể là GPL-3.0, bản cũ MIT) và của từng giọng vi-VN (model card);
- chất lượng trên 30 câu bình luận thật (số đọc thành chữ, tên đội, thuật ngữ, câu "VÀOOO!");
- tốc độ (real-time factor) trên 1 vCPU.

**Gate S2:** license cho phép dùng trong bài nộp public và đóng gói vào image; câu nghe hiểu được; RTF ≤ 0,5. Không đạt: giữ giọng trình duyệt (như hiện nay), C8 bị huỷ, README ghi đúng.

Kết quả spike ghi vào §5 (as-built) kèm số đo.

### 2.2. C2: image cho Space

**File:** `deploy/space/Dockerfile`, `deploy/space/start.sh`, `deploy/space/hf/README.md` (phần đầu YAML của Space), `deploy/space/env.space` (biến môi trường của Space, không có secret).

- **Dựng từ image chính** (Dockerfile hiện có), thêm:
  - Ollama bản CPU (xoá thư viện GPU để giảm cỡ), model nạp sẵn vào `/opt/ollama/models`;
  - Piper + file giọng (sau S2);
  - clip bình luận / recap đã warm cho 3 trận demo với seed mặc định (`pundit tts warm --provider piper`), để giám khảo nghe ngay không chờ tổng hợp;
  - bản ghi phiên model (C7) trong `data/recordings/`.
- **Chạy dưới uid 1000** (Space bắt buộc); mọi thư mục ghi được thuộc uid 1000. Cổng 8000 (`app_port: 8000`).
- **`start.sh`:** chạy `ollama serve` ở `127.0.0.1:11434` (`OLLAMA_KEEP_ALIVE=-1` để model luôn nằm trong RAM, `OLLAMA_NUM_PARALLEL=1`), chờ sẵn sàng, gửi một request làm nóng, rồi `exec pundit serve`. Ollama chết thì `start.sh` khởi động lại; trong lúc đó breaker mở và claim vẫn có câu trả lời theo luật.
- **Biến môi trường chính:**
  - LLM: `USE_MOCK_LLM=false`, `LLM_PROVIDER=ollama`, `LLM_MODEL=<theo S1>`, `LLM_LIVE_SCOPE=claims`;
  - ngân sách: `MODEL_PASS_BUDGET_S=60`, `MODEL_QUEUE_MAX=4`;
  - bản ghi: `LLM_RECORDINGS=data/recordings`;
  - giọng: `TTS_PROVIDER=piper`, `STT_PROVIDER=off`;
  - mạng: `TRUST_PROXY=true`, `MCP_ALLOWED_HOSTS` / `CORS_ORIGINS` = host của Space;
  - keepalive: `KEEPALIVE_URL`, `KEEPALIVE_INTERVAL_S=1200`.
- **Lớp 1 keepalive (trong app):** khi có `KEEPALIVE_URL`, một task nền gọi `GET <KEEPALIVE_URL>` (URL public của chính Space, đi qua proxy của Hugging Face nên được tính là có truy cập) mỗi 20 phút, có jitter. Bắt đầu ngay khi server lên, không cần ai vào xem. Kết quả lần cuối hiện ở `/healthz` (`keepalive.last_ok_at`, `failures`). Lỗi chỉ ghi log, không ảnh hưởng app. Không có biến thì tắt (dev, CI).
- **Cỡ image** mục tiêu ≤ 4 GB. Khởi động lạnh tới khi `/healthz` OK ≤ 90 s.
- **Test:**
  - task keepalive dùng đồng hồ giả: đúng chu kỳ, tắt khi thiếu biến, lỗi mạng không làm sập app;
  - job CI build image Space + smoke chạy dưới uid 1000: `/healthz`, model trả lời, Piper ra clip, `/mcp` liệt kê 9 tool.

### 2.3. C3: pipeline GitHub → GHCR → Space

**File:** `.github/workflows/space.yml`, `tools/ops/deploy_space.py`.

1. Kích hoạt: push lên `main` (khi đổi `backend/`, `frontend/`, `deploy/`) và chạy tay. Chỉ chạy khi job `ci` của cùng commit xanh.
2. Build image chính rồi image Space; push GHCR bằng `GITHUB_TOKEN` (`packages: write`), tag `sha-<commit>` + `latest`, kèm label `org.opencontainers.image.source` để package tự nối với repo.
3. `deploy_space.py` (`huggingface_hub`):
   - ghi `README.md` (phần đầu YAML) + `Dockerfile` một dòng `FROM ghcr.io/<owner>/pundit-copilot-space@sha256:<digest>` lên Space (ghim digest để Space không dùng cache cũ);
   - đặt biến của Space qua API (biến thường, không secret);
   - chờ `RUNNING`.
4. Smoke trên URL public: `/healthz` OK, `/scenarios`, MCP `tools/list` = 9 tool, một claim mẫu trả rev.1 < 2 s.
5. In URL và digest vào job summary.
6. Build arg `NEXT_PUBLIC_REPO_URL` / `NEXT_PUBLIC_VIDEO_URL` lấy từ biến repo (link video có ở M14).

Secret dùng: `HF_TOKEN` (M2). Không có secret nào trong image hay trong repo của Space.

### 2.4. C4: claim progressive

**Hành vi:**

| Bước | Khi | Người dùng thấy |
|---|---|---|
| rev.1 | < 1 s | Verdict theo luật, số liệu, bằng chứng, lời giải thích từ template. Chip "Rules · 0.3 s". Dải tiến trình "Model đang kiểm tra…" |
| model pass | nền, có hàng đợi | Tên agent đang chạy và thời gian (từ `agent_event`): ClaimAnalyst → đối chiếu → Explainer → Guardrail |
| rev.2 | khi model xong | Lời giải thích do model viết (đã qua Guardrail), kết quả đối chiếu "Model đọc giống luật" / "Model đọc khác, giữ luật". Chip "Model · qwen2.5-1.5B · live · CPU · 21 s" hoặc "Model · Phi-4-mini · recorded (Foundry Local)" |
| rev.2 bị bỏ qua | quá ngân sách, hàng đợi đầy, breaker mở, Guardrail chặn | Chip giải thích bằng lời thường ("Model bận, câu trả lời theo luật giữ nguyên"); verdict không đổi |

**Quy tắc verdict ở rev.2** (giữ luật Ngày 2):
- Luật đã đọc được claim: verdict **không đổi**. Model đọc khác: gắn `analyst_disagreement`, luật thắng.
- Luật không đọc được (INSUFFICIENT_DATA vì không đọc được) và model đưa spec hợp lệ, đúng luật "cách đọc chỉ model đưa ra phải có trong chữ của claim": verdict có thể đổi. UI hiện rõ "Cập nhật sau khi model phân tích: không đủ dữ liệu → đúng", kèm lý do.

**API:**
- `POST /claims/verify` trả rev.1 với `revision: 1`, `model_pass: queued | running | done | skipped`, `skip_reason`.
- WebSocket gửi `claim_update` (rev.2) cho mọi client của phiên.
- `GET /claims/{id}` trả bản mới nhất.
- MCP `verify_claim` giữ chỉ dùng luật (không đổi).
- Hàng đợi model: một worker (CPU), tối đa `MODEL_QUEUE_MAX` chờ; đầy thì `skipped: busy`.

**Bất biến (test):**
- với cùng output của model, rev.2 trùng kết quả của workflow một lượt hiện tại (eval không đổi số);
- rev.1 không bao giờ chờ model;
- verdict chỉ đổi theo quy tắc trên;
- một claim có tối đa 2 bản, `revision` tăng dần.

Thêm E2E: rev.1 hiện trước, rev.2 thay chip và lời giải thích (backend dùng model giả chậm).

### 2.5. C5: luồng `agent_event` và trang Agent Ops

**Backend:**
- `TraceRecorder` phát sự kiện khi span bắt đầu / kết thúc. Phát thêm sự kiện khi breaker đổi trạng thái và khi Guardrail chặn.
- Message WS `agent_event`:
  - `run_id`, `workflow` (`claim` | `studio` | `narrator`), `agent`, `phase` (`start` | `end`);
  - `status`, `source` (`llm_live` | `llm_recorded` | `rules` | `template`), `duration_ms`;
  - `handoff`: tên message có kiểu được chuyển tiếp (`ClaimContext`, `AnalystOutcome`, `ClaimAnalysis`, `EvidenceBundle`, `Judged`, `DraftForReview`, `FactPack`…);
  - `as_of` (seq, phút): shared state mà run đó ghim;
  - `breaker`, `guardrail` (codes) khi có.
- Chỉ gửi cho client đăng ký `?agents=1`. Gộp và giới hạn ≤ 20 message / s / client: ở 20x, Fan không bị nặng.
- `GET /matches/{id}/agents`: ảnh chụp trạng thái (số lần gọi, p95, số lần fallback, breaker, run gần nhất của mỗi agent).

**Trang `/agents?match=<id>`** (link từ header Studio và trang đầu):

| Vùng | Nội dung |
|---|---|
| Sơ đồ | Hai làn: Claim workflow và Studio workflow + Narrator. Node sáng khi chạy; trạng thái bằng **biểu tượng + chữ** (đang chạy, xong, fallback, bị Guardrail chặn, breaker mở), không chỉ bằng màu; cạnh ghi tên message có kiểu |
| Shared state | "MatchState @ seq 1234 · 63'": run nào đang đọc ở mốc nào |
| Nhật ký handoff | Dòng thời gian bằng lời thường, ví dụ "Guardrail chặn bản nháp của TalkingPointWriter (WRONG_COMPARISON) → dùng template" |
| Bảng agent | Vai trò một dòng, số lần gọi, p95, nguồn (live / recorded / luật / template), breaker |
| Chi tiết | Bấm node: input / output tóm tắt của span gần nhất (đã che secret, ≤ 2 KB, như drawer Agent Trace) |
| Chaos (C6) | Bảng điều khiển ở cạnh phải |

Song ngữ, nhãn Synthetic data. Ở 390 px, sơ đồ thành danh sách dọc. axe 0 serious / critical.

**Test:** hợp đồng `agent_event` (schema sinh type cho frontend), giới hạn tốc độ, component test các trạng thái node, E2E: gửi claim thì thấy chuỗi node sáng theo đúng thứ tự.

### 2.6. C6: công tắc Chaos

- **Chỉ phiên riêng** (`scenario-seed~token`; ai có mã phiên là chủ phiên). Phiên dùng chung: bảng điều khiển bị khoá, gợi ý "Bắt đầu trận demo của bạn để thử".
- **Lỗi bật được (mỗi lúc một lỗi, tự tắt sau 5 phút, nút Reset):**

  | Lỗi | Mô phỏng | Phải thấy |
  |---|---|---|
  | Model sập | Kết nối bị từ chối | Breaker mở sau N lỗi; claim vẫn trả rev.1; rev.2 `skipped: circuit_open`; Reset thì breaker half-open rồi closed |
  | Model chậm | Treo quá ngân sách | Timeout, fallback trong ngân sách |
  | Model trả rác | JSON hỏng / sai schema | Retry có feedback rồi fallback |
  | Bị giới hạn (429) | `Retry-After` | Tôn trọng `Retry-After`, không dồn request |
  | Bộ lọc nội dung | `ContentFiltered` | Fallback template, ghi lý do |
  | **Model nói dối** | Output hợp lệ nhưng một con số bị sửa | **Guardrail chặn (sai số liệu)** → template; bộ đếm "Số liệu sai đã lên màn hình: 0" |

- **Phạm vi:** wrapper bơm lỗi bọc backend LLM **của phiên đó**, breaker riêng cho phiên đang bật chaos (không mở breaker dùng chung). Với Studio trên Space (bản ghi / template), lỗi áp lên cả lớp bản ghi để Studio cũng thấy fallback.
- **API:** `POST /matches/{id}/chaos {fault, duration_s}`, có giới hạn tần suất. Trạng thái nằm trong `agent_event` và `/agents`.
- **Test:** từng lỗi cho đúng chuỗi sự kiện; phiên khác và trận chung không đổi (property test hai phiên song song); tự tắt sau thời hạn; "model nói dối" không bao giờ để số sai lên WS.

### 2.7. C7: ghi và phát lại phiên model (Foundry Local)

- **Một điểm chặn:** mọi lời gọi model đi qua `structured(system, user, schema)` (`llm/maf_backend.py`).
  - `RecordingBackend` ghi `{key, schema, output, model, provider, recorded_at, latency_ms}` với `key = sha256(prompt_version, system, user, schema)`.
  - `ReplayBackend` tra theo key: trúng thì trả output đã ghi; trượt thì chuyển tiếp (claim → model live; studio → không gọi live, để template làm).
- **Ghi** (Claude chạy trên máy bạn sau M3, chạy nền qua đêm):
  - `pundit studio` với ngân sách offline dài cho `dominant_loser-42` trước, rồi `comeback_var-7`, `red_card_swing-7`;
  - các claim mẫu trên UI + bộ eval qua `pundit eval --record`;
  - provider `openai` trỏ vào endpoint Foundry Local, model `phi-4-mini` (theo M3).
- **Phát lại đi qua toàn bộ pipeline**, Guardrail vẫn chạy. Nguồn ghi `llm_recorded`, chip "Model · Phi-4-mini · recorded (Foundry Local, 26/10)". Contract thêm giá trị `llm_recorded` cho `analyst_source` / `explanation_source` và nguồn của item studio; type frontend sinh lại.
- **Test:**
  - phát lại trong một phiên riêng mới: ≥ 95 % lời gọi studio trúng, quyết định Guardrail giống lượt ghi;
  - đổi prompt (`prompt_version`) thì không trúng nhầm;
  - bản ghi không chứa secret (quét bằng `scan_secrets.py`).
- **Cắt được:** thiếu giờ thì chỉ ghi `dominant_loser-42` (trận của video).

### 2.8. C8: Piper TTS

- `TTS_PROVIDER=piper`, một `Synthesizer` mới (`speech/piper_tts.py`) dùng chung Narrator, SpeechGuard, clip định danh theo nội dung, RateGate (đồng thời 1 để không tranh CPU với Ollama), breaker.
- **SSML → tham số Piper:** Piper không đọc SSML. Preset prosody đổi thành tốc độ (`length_scale`) và khoảng nghỉ; cao độ bỏ qua. "Vào!" / bàn thắng vẫn nhanh hơn, giải thích luật chậm hơn. Ghi khác biệt này vào as-built.
- **Giọng:** chỉ hiện nam / nữ khi S2 có đủ hai giọng đạt; còn một giọng thì ẩn nút chọn.
- **Định dạng:** WAV (pipeline đã hỗ trợ WAV từ provider mock).
- Giọng trình duyệt vẫn là dự phòng ở client khi clip lỗi (đã có).
- **Test:** provider (giả lập process), ánh xạ preset → tham số, SpeechGuard vẫn chặn mọi số đọc sai; smoke V9 với `--provider piper`.

### 2.9. C9: đánh bóng UX cho bản public

- **Trang đầu, dòng trạng thái bằng lời thường thay "LLM: mock":** "Model: qwen2.5-1.5B chạy CPU ngay trong Space · Câu studio: Phi-4-mini ghi bằng Foundry Local · Giọng: Piper (máy chủ)". Lúc model đang nạp: "Model đang khởi động (~30 s), trong lúc đó câu trả lời theo luật".
- **Bỏ mọi chữ "mock"** ở UI (giữ cho dev / CI).
- **"Thử gì":** thêm "Xem các agent làm việc (Agent Ops)" và "Thử làm hỏng model (Chaos)".
- **Header Studio:** link Agent Ops. HelpDrawer: giải thích progressive (rev.1 / rev.2) và nguồn câu chữ.
- **Testing instructions ngay trên trang đầu:** 3 bước, không cần đăng nhập, link MCP.
- **Kiểm tra lại:**
  - i18n đủ VI / EN;
  - axe 0 serious / critical;
  - bộ ảnh `shots` chụp lại cả trang mới;
  - demo-run S1–S9 (§3.12) 3/3 trên Space.

### 2.10. C10: eval và số đo

- **Eval đầy đủ** (100 claim) với model của Space (Ollama, cùng model S1) và với Foundry Local (`phi-4-mini`), chạy nền với `--resume`. `docs/eval.md` thêm hai cột, giữ cột mock và Ollama 3B. Verdict sai nào cũng sửa như lỗi an toàn trước khi ghi README.
- **Đo trên Space** (`tools/ops/measure_space.py --base-url`), với 1 và 3 người xem cùng lúc:
  - claim rev.1 p50 / p95, model pass p50 / p95, tỷ lệ `skipped`;
  - studio lag p95 (event → talking point);
  - Piper tổng hợp p95, event → âm thanh (ước tính = lag studio + tổng hợp; clip warm = 0);
  - khởi động lạnh tới `/healthz`.

  Ghi vào README, không ngoại suy.
- **Bằng chứng keepalive:** tóm tắt lịch sử watchdog (số lượt, số lần restart, thời gian Space ở `RUNNING`) cho README và testing instructions.

### 2.11. C11: tài liệu nộp bài

- **`README.md`:**
  - link demo / video;
  - "How it runs without a cloud" (một container, model mở, on-prem);
  - "Microsoft technologies" chỉ ghi cái đã dùng thật;
  - bảng eval, bảng độ trễ, bảng 5 tiêu chí;
  - Agent Ops / Chaos; ảnh chụp;
  - "Azure-ready (Bicep / azd, not deployed: no subscription was available)";
  - "Synthetic data only, not affiliated…".
- **Các tài liệu khác:** `docs/pitch.md`, `docs/copilot.md` (hai phần: MCP lúc chạy, watchdog lúc dev), `docs/mcp.md` (URL Space), `docs/eval.md`.
- **`docs/video/script.md` viết lại ≤ 1:55**, có cảnh progressive claim, Agent Ops + Chaos ("model nói dối" bị Guardrail chặn), giọng Piper; `docs/video/voiceover.txt` (mỗi dòng một cảnh, đã đo độ dài cho Clipchamp); `subtitles.srt` căn lại theo bản dựng.
- **`docs/submission.md`:** mọi ô của form nộp bài + testing instructions.
- **`claude.md` §8, `checklist.md`, §5 as-built của tài liệu này.**

### 2.12. C12: review watchdog

- Review nhánh `feat/watchdog` do Copilot viết (M4) theo tiêu chí ở §3.5: đúng hành vi, test, quyền tối thiểu, không lộ token.
- M4 chưa xong lúc 26/10 20:00: Claude viết watchdog theo cùng đặc tả. `docs/copilot.md` ghi đúng phần Copilot đã làm được.

### 2.13. C13: kết quả duyệt nhãn

- Sau M6: ghi `reviewed: {by, date}` vào 5 file eval, sửa nhãn (không sửa parser) theo ý kiến của bạn, sinh lại bảng, chạy gate, ghi lý do vào `docs/eval.md`.

---

## 3. Hạng mục bạn làm (hướng dẫn từng bước)

Lệnh viết cho **PowerShell**, chạy từ `C:\epl football hackathon`. Không dán token vào file, vào chat với Claude hay vào ảnh chụp.

### 3.1. M0: cài công cụ

```powershell
winget install --id astral-sh.uv -e
winget install --id GitHub.cli -e
```

Đóng hết terminal / VS Code, mở lại, kiểm tra: `uv --version; gh --version; docker version` (Docker Desktop phải chạy, có phần Server). Rồi:

```powershell
uv sync --project backend
cd frontend; npm ci; npm run build; cd ..
```

Gặp `os error 4551` khi `uv run`: báo Claude (Windows chặn `.venv`), không tự tắt bảo mật Windows.

### 3.2. M1: GitHub repo

1. `gh auth login` → GitHub.com → HTTPS → Login with a web browser.
2. Tạo repo và push:

   ```powershell
   gh repo create dnam2501/pundit-copilot --private --source . --remote origin --description "AI co-pilot for live football coverage: claim check, verified talking points, Vietnamese voice commentary. Synthetic data only."
   git push -u origin main
   ```

3. Repo → **Actions** → workflow `ci` phải xanh hết (nhất là `security`, gate eval, `image`). Job đỏ: gửi link lần chạy cho Claude.
4. Repo → **Settings → Actions → General** → Workflow permissions: **Read and write permissions** → Save (để `space.yml` đẩy GHCR, watchdog mở issue).
5. **Đặt public sớm** (khi `security` xanh), không đợi ngày nộp: Actions của repo public miễn phí không giới hạn phút, watchdog chạy mỗi 20 phút sẽ vượt 2000 phút / tháng nếu repo còn private. Settings → General → Danger Zone → **Change visibility → Public**.
6. Settings → General: Topics `multi-agent`, `microsoft-agent-framework`, `mcp`, `football`, `synthetic-data`, `foundry-local`.

### 3.3. M2: Hugging Face

1. **Tài khoản:** `https://huggingface.co/join` (email, không cần thẻ), xác minh email. Ghi username (ví dụ `dnam2501`).
2. **Space trống:** `https://huggingface.co/new-space`:
   - Space name: `pundit-copilot`
   - SDK: **Docker** → **Blank**
   - Hardware: **CPU basic · 2 vCPU · 16 GB · FREE**
   - Visibility: **Public**
   - **Create Space** (nội dung do `space.yml` đẩy lên, bạn không cần sửa file).
3. **Token fine-grained:** `https://huggingface.co/settings/tokens` → **Create new token** → **Fine-grained**:
   - Name `github-actions-pundit`;
   - Repositories permissions: chọn `spaces/<username>/pundit-copilot`, tick **Write access to contents/settings of selected repos**;
   - không tick quyền nào khác → **Create** → chép token.
4. **GitHub secret và variable** (repo → Settings → Secrets and variables → Actions):
   - tab **Secrets** → New repository secret: `HF_TOKEN` = token vừa tạo;
   - tab **Variables** → New repository variable:
     - `HF_SPACE` = `<username>/pundit-copilot`
     - `DEMO_URL` = `https://<username>-pundit-copilot.hf.space`

   Hoặc bằng lệnh: `gh secret set HF_TOKEN` (dán token khi được hỏi), `gh variable set HF_SPACE --body "<username>/pundit-copilot"`, `gh variable set DEMO_URL --body "https://<username>-pundit-copilot.hf.space"`.
5. Báo Claude đã xong M1 + M2 để chạy `space.yml` lần đầu (C3).
6. **Sau lần chạy đầu của `space.yml`:** `https://github.com/Hoangnam25012004?tab=packages` → `pundit-copilot-space` (và `pundit-copilot`) → **Package settings** → **Change visibility → Public** (Space phải kéo được image mà không đăng nhập). Bảo Claude chạy lại `space.yml`.
7. **Kiểm tra:**
   - Space → tab **Logs** chuyển **Running**;
   - mở **link trực tiếp** `https://<username>-pundit-copilot.hf.space` (nút ⋮ → Embed this Space → Direct URL). Đây là link demo nộp bài; không dùng link `huggingface.co/spaces/…` vì app nằm trong iframe.

### 3.4. M3: Foundry Local

1. `winget install Microsoft.FoundryLocal`, mở terminal mới, `foundry --version`.
2. `foundry model list` → tìm `phi-4-mini`. `foundry model run phi-4-mini` (lần đầu tải vài GB). Gõ thử một câu, thoát chat.
3. `foundry service status` → ghi endpoint (ví dụ `http://127.0.0.1:<port>`). Lấy id model: `Invoke-RestMethod http://127.0.0.1:<port>/v1/models | ConvertTo-Json -Depth 4`.
4. Gửi Claude endpoint + id model. Claude thử một claim rồi bắt đầu ghi phiên (C7) và eval (C10), chạy nền qua đêm: **để máy cắm sạc, tắt chế độ ngủ** (Settings → System → Power → Screen and sleep: Never trong đêm 26/10).
5. Không cài / chạy được sau 90 phút: báo Claude. C7 ghi bằng Ollama `qwen2.5:3b` (đã có), README không nhắc Foundry Local.

### 3.5. M4: watchdog bằng GitHub Copilot Agent Mode (lớp 2)

**Vì sao giao việc này cho Copilot:** việc thật và cần thiết (giữ demo sống tới 10/11), gói gọn, đặc tả và test rõ, tách khỏi lõi app (Copilot sai cũng không làm hỏng demo), và dùng thêm GitHub CLI / Actions (hero technology). Lớp 1 và lớp 3 vẫn giữ Space thức trong lúc bạn làm.

1. `git switch -c feat/watchdog`. VS Code → Copilot Chat (`Ctrl+Alt+I`) → chế độ **Agent** → chat mới; ghi lại model đang chọn.
2. Đính kèm ngữ cảnh: `.github/workflows/ci.yml`, `tools/security/` (để Copilot theo cách tổ chức tool + test của repo), `docs/day7-design.md`.
3. Dán prompt:

   ```text
   Goal: a watchdog that keeps the public demo of Pundit Copilot (a Hugging Face Docker Space)
   awake and healthy until 2026-11-10, with nobody visiting it.

   Files to create:
   - tools/ops/watchdog.py (Python 3.12, standard library + huggingface_hub + httpx, exact pins)
   - tools/ops/test_watchdog.py (pytest, no network: fake the HF API, HTTP and gh)
   - .github/workflows/watchdog.yml

   Behaviour of one run (python tools/ops/watchdog.py):
   1. Read HF_SPACE (owner/name), DEMO_URL (https://...hf.space) and HF_TOKEN from the environment.
   2. Read the Space runtime with huggingface_hub.HfApi().get_space_runtime(HF_SPACE): stage and
      sleep time; print them.
   3. Probe DEMO_URL/healthz (timeout 20 s): healthy only if HTTP 200, JSON, and status == "ok".
      Then DEMO_URL/scenarios must return a non-empty JSON list.
   4. If the stage is RUNNING and the probe is healthy: nothing to do.
   5. If the probe fails while RUNNING: wait 60 s and probe again; restart only if it fails twice.
   6. If the stage is SLEEPING, STOPPED, RUNTIME_ERROR or BUILD_ERROR, or step 5 failed twice:
      call HfApi().restart_space(HF_SPACE), then poll every 30 s for up to 10 minutes until the
      stage is RUNNING and the probe is healthy. If the stage is PAUSED (paused by hand), do not
      restart: report it.
   7. Report with the GitHub CLI (`gh`, called through subprocess, injectable for tests):
      on failure after the restart, or on PAUSED, open an issue titled "Demo is down" with label
      "demo-down" (or comment on the open one); when healthy again and such an issue is open,
      comment "Recovered at <UTC time>" and close it.
   8. Append a one-line Markdown summary (time, stage, probe latency, action) to
      $GITHUB_STEP_SUMMARY when it is set. Exit code 0 when healthy at the end, 1 otherwise.
   Never print or log HF_TOKEN or any header.

   Workflow: schedule "*/20 * * * *" plus workflow_dispatch; permissions issues: write and
   contents: read only; concurrency group "watchdog" without cancel-in-progress; timeout 15 min;
   env HF_SPACE and DEMO_URL from repository variables, HF_TOKEN from secrets; GH_TOKEN =
   github.token for gh. Use actions/checkout and actions/setup-python pinned to a major version.

   Tests (pytest): healthy -> no restart; SLEEPING -> restart then healthy; RUNTIME_ERROR ->
   restart; probe fails once then succeeds -> no restart; restart never gets healthy -> issue
   opened and exit 1; healthy with an open demo-down issue -> issue closed; PAUSED -> issue, no
   restart; the token never appears in captured output.
   Run `uv run --with pytest --with huggingface_hub --with httpx pytest tools/ops -q` until green.
   Follow the style of tools/security: short docstrings, type hints, no comments that restate
   the code.
   ```

4. Đọc từng lệnh terminal Copilot muốn chạy trước khi **Allow**. Không cho lệnh xoá file ngoài `tools/ops/` hay đổi cấu hình git.
5. **Tự kiểm tra:**
   - `git diff --stat` chỉ có 3 file trên (+ CI nếu Copilot thêm bước chạy test);
   - test xanh;
   - xoá tạm dòng gọi `restart_space` → test SLEEPING phải đỏ → hoàn tác.
6. Commit: `git add tools/ops .github/workflows/watchdog.yml; git commit -m "feat(ops): watchdog keeps the demo Space awake and reports outages"` (thân commit ghi "Written with GitHub Copilot Agent Mode, see docs/copilot.md section 2"). Push nhánh: `git push -u origin feat/watchdog`.
7. Bảo Claude **review (C12)**. Sửa theo review, rồi merge: `git switch main; git merge --no-ff feat/watchdog; git push`.
8. Repo → **Actions → watchdog → Run workflow** → xanh, summary ghi `RUNNING`.
9. **Thử thật một lần:** Space → Settings → **Restart this Space** (hoặc Pause rồi Resume) → chạy watchdog bằng tay trong lúc Space đang khởi động → ghi lại kết quả.
10. **Ghi vào `docs/copilot.md` phần 2:** prompt, model, Copilot làm gì (file, lệnh, số vòng), lỗi nó mắc, bạn sửa tay gì, thời gian; 1–2 ảnh phiên Agent Mode (`docs/img/copilot/dev-watchdog.png`). Claude định dạng lại ở C11.

**Quá 26/10 20:00 chưa xong:** dừng, giữ nhánh, báo Claude (C12 viết thay). Ghi đúng phần Copilot đã làm.

### 3.6. M5: UptimeRobot (lớp 3)

Sau F1 (Space đã Running):

1. `https://uptimerobot.com` → Sign up (miễn phí) → xác minh email.
2. **Add New Monitor:**
   - Monitor Type **Keyword**;
   - URL `https://<username>-pundit-copilot.hf.space/healthz`;
   - Keyword `"status":"ok"` (Alert when keyword **not exists**);
   - Interval **5 minutes**;
   - Alert contact: email của bạn → **Create Monitor**.
3. Sau 10 phút: monitor **Up**, có 2 lượt kiểm tra.

**Ba lớp cùng chạy:**

| Lớp | Tần suất | Giữ thức | Đánh thức khi đã ngủ | Báo lỗi |
|---|---|---|---|---|
| 1. Tự ping trong app | 20' | Có, từ lúc khởi động | Không | Log, `/healthz` |
| 2. Watchdog Actions | 20' (cron của Actions có thể trễ) | Có | **Có** (restart qua API) | Issue GitHub → email |
| 3. UptimeRobot | 5' | Có | Có (request đánh thức Space) | Email |

Space miễn phí chỉ ngủ sau **48 giờ không có truy cập**; mỗi lớp một mình đã giữ được, ba lớp phòng khi một lớp hỏng.

### 3.7. M6: duyệt bảng nhãn eval

File `backend/evals/review/label-sheet.md` (70 claim, 5 phần). Với mỗi dòng:

1. Đọc claim và cột `at` (`FT` = hết trận, `60'` = phút 60).
2. So **Stats Tools** với **independent count**: phải bằng nhau. Chữ đậm `≠ Stats Tools` hoặc `label written from …` là dấu hiệu sai.
3. Kiểm nhãn theo luật Ngày 2 ([day2-design.md](day2-design.md) §5):
   - lệch 1 khi giá trị ≥ 5 → PARTLY_TRUE; xG dung sai 0.05;
   - "áp đảo" cần 3 / 4 chỉ số;
   - câu không đo được → INSUFFICIENT_DATA;
   - câu chứa lệnh không bao giờ TRUE.
4. Ghi `OK` hoặc nhãn đúng + lý do ngắn vào cột **review**.
5. Tự kiểm một số: `uv run --project backend pundit stats --scenario comeback_var --seed 7`, hoặc `uv run --project backend pundit claim "<claim>" --scenario comeback_var --seed 7 --trace`.

Xong: lưu file, báo Claude **C13**.

### 3.8. M7: xác nhận 20 câu giải thích của Ollama

`backend/evals/review/ollama-explanations.md`: đọc từng câu, ghi `đồng ý` hoặc ý kiến của bạn cạnh cột "Claude đọc trước". Gửi Claude các dòng không đồng ý.

### 3.9. M8: Copilot Agent Mode hỏi trận qua MCP

1. **Local:** `uv run --project backend pundit serve --ui frontend/out`. Trong VS Code mở `.vscode/mcp.json` (server `pundit-live`), bấm **Start** → **Running**, 9 tool.
2. **Space:** thêm vào `.vscode/mcp.json` một server nữa:

   ```json
   "pundit-public": { "type": "http", "url": "https://<username>-pundit-copilot.hf.space/mcp" }
   ```

   Start → Running.
3. Copilot Chat → **Agent** → biểu tượng **Tools**: thấy tool của cả hai server. Ghi lại model Copilot.
4. Trên Space, bấm **Bắt đầu trận demo của tôi**, chép mã phiên (`dominant_loser-42~xxxxxxxx`) cho câu 6.
5. Hỏi 6 câu trong bảng của `docs/copilot.md`, **mỗi câu một chat mới**. Câu 1–5 dùng `pundit-public`; câu 6 hỏi phiên riêng trên Space. Khi được hỏi: **Allow**.
6. Mỗi câu: mở rộng tool call xem input / output; chụp màn hình (`Win+Shift+S`) → `docs/img/copilot/q1.png` … `q6.png`; so số Copilot nói với output của tool.
7. Điền cột **Copilot's run**. Copilot không gọi tool hoặc nói số không có trong output: ghi đúng vào "What Copilot got wrong", không chạy lại để chọn lần đẹp mà không ghi lần trước.

### 3.10. M9: nghe duyệt giọng Piper

Sau C8 (local hoặc Space):

1. Fan: Casual · Tiếng Việt · Harbor · Người mới xem · giọng bật; chạy `dominant_loser-42` ở 5x, rồi `comeback_var-7` (việt vị, VAR).
2. Ghi bảng lỗi: phút, câu hiển thị, lỗi nghe được (đọc sai số, tên đội, thuật ngữ, ngắt câu, chữ tiếng Anh đọc kiểu Việt). Gửi Claude: mỗi lỗi thành một alias / luật kèm test.
3. Thử: bàn thắng có nhanh hơn không; câu giải thích luật có chậm hơn không; Pause ở Studio khi đang đọc (đọc nốt câu rồi dừng); recap giờ nghỉ: nghe, tua, nghe lại.

### 3.11. M10: bug bash trên Space

Dùng **link trực tiếp**, không đăng nhập Hugging Face:

| Thiết bị / trình duyệt | Kịch bản |
|---|---|
| Edge InPrivate, laptop 1366 × 768 | S1–S9 (§3.12) |
| Chrome (hoặc Firefox) | S1, S2, S5 (giọng Piper phải nghe được dù trình duyệt không có giọng Việt) |
| Điện thoại thật qua **4G** | trang đầu, Fan bật giọng bằng chạm, Agent Ops dạng dọc |
| Hai trình duyệt cùng lúc, hai phiên riêng | Bật Chaos ở phiên A: phiên B không bị ảnh hưởng |

Ghi mỗi lỗi: trang, bước, thấy gì, mong đợi gì, ảnh chụp. Gửi Claude. Mức Blocker / Major (day6 §8.2) sửa trước F3.

### 3.12. Kịch bản demo chuẩn (S1–S9)

Dùng cho bug bash, quay video và testing instructions:

| Bước | Việc | Phải thấy |
|---|---|---|
| S1 | Trang đầu → **Bắt đầu trận demo của tôi** | Studio phiên riêng chạy 20x, nhãn Synthetic data, dòng trạng thái model / giọng bằng lời thường |
| S2 | Claim "Đội nhà áp đảo hiệp 2" | rev.1 < 1 s (TRUE, 61 % – 39 %, 15–1, xG 2.24–0.24); tiến trình model; rev.2 với lời giải thích của model + "Model đọc giống luật" |
| S3 | Why / Evidence / Agent Trace | Event ID, sơ đồ sân, span các agent + Guardrail |
| S4 | Duyệt + ghim talking point; Overlay | Lower-third lên rồi xuống; chip nguồn (recorded / template) |
| S5 | Fan: casual · vi · HAR · Người mới xem · giọng | Giọng Piper; bàn thắng nhanh hơn; việt vị được giải thích |
| S6 | Giờ nghỉ → recap | Nghe, tua, nghe lại |
| S7 | So sánh Analyst / Casual | Cùng khoảnh khắc, hai cách viết |
| S8 | **Agent Ops** | Node sáng theo thứ tự khi gửi claim; handoff có kiểu; shared state `as_of` |
| S9 | **Chaos**: "Model nói dối" rồi "Model sập", gửi claim, Reset | Guardrail chặn số sai (bộ đếm 0); breaker mở, rev.1 vẫn có; Reset → breaker đóng lại |

### 3.13. M11: voice-over bằng Clipchamp

Sau C11 (`docs/video/voiceover.txt`, mỗi dòng một cảnh):

1. Start → **Clipchamp** → đăng nhập tài khoản Microsoft → **Create a new video**.
2. Thanh bên trái **Record & create → Text to speech** → Language **English (US)**, chọn giọng (nghe thử 2–3 giọng, giữ một giọng cho cả video), Speed 1.0 → dán dòng 1 → **Save to media**. Lặp cho mọi dòng.
3. Mỗi clip voice-over phải ngắn hơn thời lượng cảnh trong `script.md`. Dòng nào dài: báo Claude rút gọn câu, không tăng tốc quá 1.1.

### 3.14. M12: quay video

Kịch bản: `docs/video/script.md` bản mới (C11). Quay **từng cảnh một clip**, mỗi cảnh vài lần.

**Chuẩn bị máy:**
1. Windows: **Do not disturb** bật, taskbar tự ẩn, tắt Zalo / ứng dụng hay hiện thông báo.
2. Màn hình 1920 × 1080: đặt **Scale 100 %** khi quay.
3. Edge **InPrivate**, zoom 100 %, `F11` khi quay (không lộ thanh địa chỉ / logo trình duyệt).
4. **App:**
   - quay trên **link Space** (đúng thứ giám khảo thấy);
   - mạng chậm thì chạy cùng image ở máy: `docker run --cpus 2 -p 8000:8000 ghcr.io/hoangnam25012004/pundit-copilot-space:latest` (Claude đưa lệnh đủ biến);
   - với cảnh 2, đợi model đã nóng (gửi một claim thử trước khi quay).
5. **OBS** (profile `Pundit` 1920 × 1080, mở bằng PowerShell `Start-Process`):
   - Output → Recording: `mkv`, encoder phần cứng nếu có, "High Quality, Medium File Size";
   - Video: 1920 × 1080, 30 FPS;
   - Audio: **Desktop Audio bật** (thu giọng Piper), **Mic tắt**;
   - Scene **Screen**: Window Capture (Edge);
   - Scene **On air**: Color Source nền tối (không dùng cảnh quay trận thật) + Browser Source `<link Space>/overlay?match=<mã phiên>` 1920 × 1080 + Text nhỏ "Synthetic data";
   - quay **đầu ra của OBS**, không quay cửa sổ OBS.
6. Thử âm: phát một câu bình luận, thanh Desktop Audio nhảy, không chạm đỏ.

**Mẹo:** cảnh cần chờ (bàn thắng, việt vị) thì tăng tốc trận tới gần thời điểm đó, Pause, Start Recording, rồi Resume ở 1x / 5x. Cảnh Chaos: bật lỗi trước, rồi mới gửi claim để thấy cả chuỗi trên Agent Ops. Xong: **File → Remux Recordings** → `.mp4`.

**Không được lộ:** email, token, trang Settings của Hugging Face / GitHub, thanh địa chỉ có thông tin riêng.

### 3.15. M13: dựng video và phụ đề (Clipchamp)

1. Import các clip `.mp4`, xếp theo cảnh; cắt theo thời lượng trong `script.md`.
2. Đặt voice-over dưới từng cảnh. Cảnh giọng Piper: giảm voice-over xuống ~30 % hoặc bỏ, để giọng tiếng Việt nổi (phụ đề mang nghĩa).
3. Thẻ đầu / cuối: Text "Pundit Copilot" + "Synthetic data only. Not affiliated with any league or club." **Không nhạc nền, không sticker / stock media có logo.**
4. **Phụ đề tiếng Anh cho mọi câu tiếng Việt:**
   - Text dạng phụ đề ở đáy khung, lấy bản dịch trong `docs/video/subtitles.srt`;
   - gửi Claude thời điểm thật của từng câu để căn lại `subtitles.srt` cho YouTube.
5. Kiểm tra trước khi xuất: **≤ 1:55**; không nhạc; không logo trình duyệt / OBS / Hugging Face; nhãn Synthetic data ở mọi cảnh có app; mọi câu tiếng Việt có chữ tiếng Anh.
6. **Export 1080p** → `C:\pundit-video\pundit-copilot-demo.mp4`. Xem lại một lần có tiếng, một lần tắt tiếng.

### 3.16. M14: upload YouTube

1. `https://studio.youtube.com` → tạo kênh nếu chưa có → **Create → Upload videos**.
2. **Details:**
   - Title: `Pundit Copilot: an AI co-pilot for live football coverage (synthetic data)`;
   - Description: bản pitch 60 từ + "Synthetic data only. Not affiliated with any league or club." + link repo + link demo;
   - Audience: **No, it's not made for kids**.
3. **Subtitles** → English → **Upload file → With timing** → `docs/video/subtitles.srt`.
4. **Checks**: "No issues found". **Visibility: Public** → Publish. Chép `https://youtu.be/<id>`.
5. Mở link trong InPrivate: phát được, có phụ đề, thời lượng < 2:00.
6. Đặt biến repo `NEXT_PUBLIC_VIDEO_URL` = link video, `NEXT_PUBLIC_REPO_URL` = URL của repo (`https://github.com/Hoangnam25012004/<tên repo>`; không đặt thì `space.yml` tự dùng URL của repo), rồi bảo Claude chạy `space.yml` để trang đầu có link.

### 3.17. M15: kiểm tra cuối (go / no-go)

| # | Kiểm tra | Cách |
|---|---|---|
| 1 | Secret / tuân thủ | CI `security` xanh trên commit cuối; `python tools/security/scan_secrets.py --history --worktree --build`; `python tools/security/scan_compliance.py` |
| 2 | Không track `.env` | `git ls-files \| Select-String "\.env$"` rỗng |
| 3 | Repo public, README hiện đúng trên GitHub | InPrivate; sơ đồ mermaid hiện; mọi link mở được |
| 4 | Link demo | InPrivate trên máy khác hoặc điện thoại 4G: S1–S9 |
| 5 | Demo-run 3 lần trên Space | Claude chạy `demo.spec.ts --repeat-each=3` với URL Space |
| 6 | Keepalive | Watchdog: các lượt gần nhất xanh, không có issue `demo-down` mở; UptimeRobot Up; `/healthz` có `keepalive.last_ok_at` < 25 phút trước |
| 7 | Video | Public, < 2:00, có phụ đề, xem được khi không đăng nhập |
| 8 | MCP public | Copilot hỏi câu 1 qua `pundit-public` |
| 9 | Tag bản nộp | `git tag -a v1.0-submission -m "Hackathon submission"; git push origin v1.0-submission` |

### 3.18. M16: nộp bài (Innovation Studio)

1. Trang hackathon trên Innovation Studio → đăng nhập → Submit / Manage submission.
2. Điền từ `docs/submission.md` (C11):
   - tên dự án;
   - pitch (làm gì, công nghệ Microsoft nào, giải quyết vấn đề gì);
   - link video, link repo, **link trực tiếp** của Space;
   - testing instructions: không cần đăng nhập, S1–S9 rút gọn, địa chỉ MCP;
   - hạng mục: **Grand Prize** + **Best Multi-Agent System**;
   - công nghệ: chỉ ghi cái đã dùng thật.
3. Ô nào không có trong `submission.md`: chép tên ô gửi Claude, không điền vội.
4. **Save draft** → đọc lại → mở từng link từ chính form → **Submit**.
5. Chụp trang xác nhận + email xác nhận, lưu ngoài repo (`C:\pundit-video\submitted.png`). Báo Claude giờ nộp.

### 3.19. M17: vận hành tới 10/11

- **Không đổi tính năng** sau hạn nộp: bản giám khảo xem là tag `v1.0-submission`. Chỉ xử lý vận hành. Buộc phải sửa lỗi chặn demo: ghi trong README mục "Changes after submission".
- **Mỗi ngày (~10 phút):**
  - mở link demo, chạy S1–S2;
  - xem watchdog (Actions) và UptimeRobot;
  - có email "Demo is down" thì mở Space → Logs, thử Restart; không được thì báo Claude.
- **Trước 10/11:** GitHub tắt cron của repo public khi repo không có hoạt động trong 60 ngày (không xảy ra trước 10/11); token Hugging Face không có hạn trừ khi bạn đặt.
- **Từ 11/11:** Space → Settings → Pause (hoặc để nguyên làm portfolio); tắt workflow watchdog (Actions → watchdog → ⋯ → Disable workflow); xoá token `github-actions-pundit`; pause monitor UptimeRobot.
- Người thắng công bố chậm nhất 20/11; nếu thắng, nộp giấy tờ xác minh trong 10 ngày làm việc.

---

## 4. Lịch và thứ tự cắt

| Khi | Claude | Bạn |
|---|---|---|
| **25/10 tối** | C1 (S1 bằng Docker 2 CPU, S2), bắt đầu C4 | M0, M1, M2, bắt đầu M6 |
| **26/10 sáng** | C2, C3 → **F1 14:00** (Space + lớp 1); C8 | M3 (Foundry Local), bắt đầu M4 |
| **26/10 chiều** | C4 xong, C5; bắt đầu ghi C7 (nền) | M5 (sau F1), M4 tiếp, M9 |
| **26/10 tối** | C6, C9, C12; eval C10 chạy nền qua đêm → **F2 23:00** | M4 xong trước 20:00, M7, M8 |
| **27/10 sáng** | Sửa lỗi bug bash, C10 đo trên Space, C13, C11 kịch bản + voice-over | M10 (trước 09:00), M11, **M12 quay 09:00–13:00** |
| **27/10 chiều** | C11 README / submission, build lại có link | M13, M14 |
| **27/10 tối** | Demo-run 3/3 trên Space | M15, **M16 nộp trước 22:00** |
| 28/10 → 13:59 | Dự phòng sự cố | Dự phòng |

**Thứ tự cắt khi trễ** (cắt mục đầu trước):
1. Eval Foundry Local (giữ phần ghi phiên);
2. ghi phiên `red_card_swing-7`, rồi `comeback_var-7` (giữ `dominant_loser-42` của video);
3. Chaos: chỉ giữ "Model sập" và "Model nói dối";
4. độ bóng sơ đồ Agent Ops (giữ bảng + nhật ký handoff);
5. M7;
6. phụ đề dạng file trên YouTube (giữ phụ đề in thẳng lên video).

**Không cắt:** Space + ba lớp keepalive, claim progressive, Piper (nếu qua S2; không qua thì giọng trình duyệt), Agent Ops tối thiểu, M6, M8, video, tài liệu nộp, nộp bài.

---

## 5. Rủi ro và phương án

| Rủi ro | Khả năng | Dấu hiệu | Phương án |
|---|---|---|---|
| Model trên CPU quá chậm / sai | Trung bình | Gate S1 | Model nhỏ hơn; không qua thì luật + bản ghi Foundry Local, README ghi rõ |
| Nhiều giám khảo cùng gửi claim | Trung bình | Hàng đợi đầy | rev.1 luôn có ngay; rev.2 `skipped: busy` bằng lời thường; trần phiên 16 giữ nguyên |
| Piper + Ollama tranh CPU | Trung bình | Lag studio tăng | Clip warm sẵn cho trận demo; RateGate Piper = 1; đo ở C10 |
| License Piper / giọng không đạt | Trung bình | Gate S2 | Giọng trình duyệt; Fan báo rõ khi thiết bị không có giọng Việt |
| Space ngủ / Hugging Face khởi động lại | Thấp (có 3 lớp) | Watchdog / UptimeRobot báo | Watchdog restart; phiên trong bộ nhớ mất nên giám khảo bấm lại "Bắt đầu trận demo"; testing instructions ghi chú |
| Hugging Face coi ping là lạm dụng | Thấp | Space bị tạm dừng | Tần suất vừa phải (20', 5'); watchdog báo `PAUSED` qua issue; phương án dự phòng: chạy image ở máy và cung cấp link khác trong testing instructions |
| Cron của Actions trễ / bỏ lượt | Trung bình | Summary thưa | Lớp 1 và 3 độc lập |
| Image quá lớn, Space build lâu | Thấp | > 4 GB | Xoá thư viện GPU của Ollama; model Q4; build trên GitHub, Space chỉ kéo image |
| Ghi phiên Foundry Local không kịp | Trung bình | Lượt ghi chạy chậm | Chỉ `dominant_loser-42`; studio còn lại dùng template (nhãn đúng) |
| Copilot viết watchdog không xong | Trung bình | Quá 20:00 26/10 | Claude viết (C12); ghi đúng phần Copilot đã làm |
| Vòng 1 coi thiếu Azure là thiếu hero technology | Trung bình | – | Agent Framework, Foundry Local, Copilot Agent Mode, GitHub CLI / Actions, MCP đều dùng thật và ghi rõ; Bicep sẵn sàng; không tuyên bố điều chưa làm |
| Video quá 2 phút | Trung bình | Clipchamp > 1:55 | Cắt cảnh So sánh còn 12 s, Kiến trúc còn 10 s |
| Lộ secret khi repo public | Thấp | CI `security` đỏ | Không đổi public khi đỏ; lộ thật thì thu hồi token ngay |

---

## 6. Điều kiện hoàn thành

- [ ] Space chạy trên link trực tiếp, không đăng nhập; S1–S9 đạt; demo-run 3/3
- [ ] Ba lớp keepalive bật từ F1; watchdog xanh liên tục, không có issue `demo-down` mở lúc nộp
- [ ] Claim progressive: rev.1 p95 < 1 s; rev.2 do model live hoặc đã ghi, đúng nhãn nguồn
- [ ] Agent Ops + Chaos chạy trên Space; "Model nói dối" không bao giờ đưa số sai lên màn hình
- [ ] Giọng tiếng Việt nghe được trên thiết bị không có giọng Việt (Piper), hoặc README ghi rõ giới hạn
- [ ] Eval: 0 verdict sai, 0 số không nguồn với model của Space; bảng nhãn đã duyệt
- [ ] Không còn chữ "mock" trên UI public; mọi nguồn câu chữ được gọi đúng tên
- [ ] `docs/copilot.md`: 6 lượt MCP + phiên watchdog, ghi đúng như đã xảy ra
- [ ] README tiếng Anh: chỉ việc đã chạy thật, eval, độ trễ trên Space, bảng tiêu chí, "Azure-ready, not deployed"
- [ ] Video < 2:00, public, phụ đề tiếng Anh, không nhạc, không logo bên thứ ba
- [ ] Repo public, tag `v1.0-submission`, CI xanh; nộp trước 27/10 22:00, có ảnh xác nhận

---

## 7. As-built

_(Claude điền khi làm: kết quả S1 / S2, khác biệt so với thiết kế, kết quả kiểm thử, số đo trên Space, lịch sử keepalive.)_

### 7.1. C1 / S1 (25/10, máy dev, `docker run --cpus 2 --memory 7g`)

Docker Desktop trên máy này chỉ có 8 GB RAM nên không chạy được `--memory 16g`; model 1,5B dùng dưới 2 GB nên không ảnh hưởng.

| Lượt | Cấu hình | Model pass p50 / p95 | Model dùng được | Verdict sai | Số không nguồn |
|---|---|---|---|---|---|
| 1 | `qwen2.5:1.5b`, llama.cpp tự chọn 8 luồng | 136 s / 198 s | 0 / 20 (hết giờ 90 s) | 0 | 0 |
| 2 | như trên, **2 luồng** | 114 s / 160 s | 8 / 20 (4 spec đều bị luật bác; 7 lời giải thích qua Guardrail, 24 bản nháp bị chặn) | 0 | 0 |

- **Lỗi thật tìm ra:** llama.cpp đếm số lõi của máy (16), không theo hạn mức 2 CPU của container, nên chạy 8 luồng trên 2 CPU. Space cũng giới hạn bằng cgroup, nên sẽ gặp đúng lỗi này. Sửa: `warm_model.py` đọc `/sys/fs/cgroup/cpu.max` và đặt `num_thread` cho model (cùng tên, qua `/api/create`), vì Ollama 0.40 không có biến môi trường cho số luồng. Test: `tools/ops/test_ops.py`.
- **Tốc độ:** sinh chữ 3,5–6 token/s, đọc prompt 15–30 token/s. Sinh chữ thường (không JSON schema) cũng chỉ 5,1 token/s, nên chỗ nghẽn là CPU mà Docker Desktop cấp, không phải ràng buộc schema.
- **Gate S1: không đạt** ở máy dev (p95 160 s so với ngưỡng 45 s). Theo §2.1 phải đo lại trên Space thật (C10) trước khi chốt. Nếu Space cũng không đạt, chọn giữa `qwen2.5:0.5b` (nhanh hơn, nhưng 1,5B đã có 10/20 câu trả lời không dùng được) và chỉ dùng luật + bản ghi Foundry Local (C7 thành P0).
- **Tạm thời, tới khi có C4:** `env.space` đặt `LLM_TIMEOUT_S=40`, `CLAIM_BUDGET_S=45`. Đo thật: một claim trả lời sau 45,2 s, verdict `TRUE` theo luật, cờ `analyst_fallback` + `explainer_fallback`. Đổi được bằng biến của Space mà không build lại.

### 7.2. C2 / C3 (25/10)

- **Image Space:** 2,58 GB (mục tiêu ≤ 4 GB). Ollama **v0.40.2** (08/10, kiểm SHA-256 của bản phát hành; bản 0.40.10 trên máy dev mới ra 2 ngày) chỉ giữ thư viện CPU, giữ nguyên các file license đi kèm; model nạp sẵn 941 MB; chạy dưới uid 1000, thư mục model thuộc uid 1000 (Ollama ghi manifest lúc khởi động). Khởi động lạnh tới `/healthz` OK: 26 s (mục tiêu ≤ 90 s); model nạp trong 15–19 s, chạy nền, không chặn server.
- **Khác thiết kế:**
  - Biến phụ thuộc host (`KEEPALIVE_URL`, `CORS_ORIGINS`, `MCP_ALLOWED_HOSTS`) lấy từ `SPACE_HOST` mà Hugging Face đặt sẵn trong container, không đặt qua API, nên `deploy_space.py` không cần quyền sửa biến của Space. Biến của Space vẫn thắng mọi giá trị mặc định trong `env.space`.
  - `LLM_LIVE_SCOPE` (`all` / `claims`) làm trong C2 thay vì C4: với `claims`, studio và recap dùng template, recap theo luật.
  - Thêm `build` vào `/healthz` (`PUNDIT_BUILD` = commit rút gọn): smoke chờ đến khi Space trả lời bằng đúng commit vừa deploy, không bị container cũ đánh lừa. `space.yml` cũng dùng nó để bỏ qua deploy khi từ commit Space đang chạy tới giờ không đổi gì trong `backend`, `frontend`, `deploy`, `Dockerfile`.
  - Không thêm job build image Space vào `ci.yml` (2,6 GB, thêm vài phút mỗi push): image Space được build và smoke trong `space.yml` trên URL public. Test của `tools/ops` chạy trong job `security`.
  - Piper chưa có (C8): `TTS_PROVIDER=browser`.
- **`space.yml`:** chạy sau khi `ci` xanh trên `main` (`workflow_run`) hoặc chạy tay (`force`). Build image chính rồi image Space, push GHCR (`sha-<commit>` + `latest`, label `org.opencontainers.image.source`), `deploy_space.py` (`huggingface_hub==2.2.0`) ghi `README.md` + `Dockerfile` `FROM …@sha256:…` lên Space và chờ `RUNNING`, rồi `smoke_space.py`: `/healthz` đúng build, `/`, `/scenarios`, MCP `tools/list` = 9, `verify_claim` "Đội nhà áp đảo hiệp 2" = `TRUE` < 5 s. Smoke chạy với container local: đạt, claim 0,11 s.
- **Space hiện có là `static`** (tạo nhầm loại), host `hnam2501-pundit-copilot.static.hf.space`. `deploy_space.py` ghi `sdk: docker` vào README; nếu sau commit Space vẫn không phải Docker thì script dừng và báo tạo lại Space loại Docker → Blank.
- **Keepalive lớp 1:** container local với `KEEPALIVE_INTERVAL_S=60`: lượt ping đầu sau 60 s, HTTP 200, `/healthz` ghi `last_ok_at`.
- **Lần deploy đầu (25/10):** build + push xong; Space chuyển `static` → Docker được bằng README, nhưng đứng ở `PAUSED`: "Quota exceeded for flavor cpu-basic (requested=1): current=0, limit=0" (tài khoản Hugging Face mới tạo cùng ngày). `deploy_space.py` nay coi `PAUSED` là lỗi ngay, in lý do của Hugging Face kèm gợi ý.

### 7.3. C4, C5, C6, C9, C11, spike bổ sung (26/10)

- **S1 với `qwen2.5:0.5b`** (2 CPU, 2 luồng): p50 21 s, p95 58 s, spec của model dùng được 0 / 20, 7 lời giải thích qua Guardrail; **gate không đạt** → giữ `qwen2.5:1.5b` (quyết định của bạn: chỉ đổi khi 0.5b qua gate).
- **S2 Piper:** engine `rhasspy/piper` 2023.11.14-2 (MIT, repo đã lưu trữ; đóng gói espeak-ng GPL-3.0 nên chạy như process riêng). Giọng vi-VN: `vais1000` (CC BY 4.0) đạt license; `25hours_single` (license không rõ) và `vivos` (CC BY-NC-SA) bị loại. RTF trên 30 câu bình luận thật (số đã đọc thành chữ): 1 CPU 0,77 (một process liên tục) / 0,85 (mỗi câu một process, `OMP_NUM_THREADS=1`); 2 CPU 0,39 / 0,51. **Gate (RTF ≤ 0,5 trên 1 vCPU) không đạt ở máy dev** → C8 không làm, Space giữ giọng trình duyệt. 30 file WAV mẫu để nghe (M9) nằm trong scratchpad của phiên.
- **C4 claim progressive** (`CLAIM_PROGRESSIVE`, `MODEL_PASS_BUDGET_S=60`, `MODEL_QUEUE_MAX=4`; bật trong `env.space`):
  - rev.1 = chính workflow claim không có model (`verify_rules`); rev.2 = workflow một lượt chạy trên một worker cho mọi phiên, nên rev.2 trùng kết quả eval. Kiểm trên 100 claim eval: rev.2 = một lượt; verdict chỉ đổi khi luật không đọc được (`unsupported_claim`, `unknown_entity`, `ambiguous_entity`).
  - Khác thiết kế: không có message `claim_update`; cùng message `claim`, cùng id, mang `revision` / `model_pass` (queued → running → rev.2). Store của UI và phiên đều không cho bản cũ đè bản mới.
  - Ở rev.1, cờ `explainer_fallback` bị bỏ (mẫu là thiết kế của rev.1, không phải sự cố).
  - Đo trên image Space (`--cpus 2`, máy dev): rev.1 sau **0,1 s**; lượt model dùng hết 60 s rồi `skipped: no_answer`, câu trả lời theo luật giữ nguyên. Số đo trên Space thật: C10.
- **C5 Agent Ops:**
  - Khác thiết kế: **WebSocket riêng `/ws/agents/{id}`** thay cho `?agents=1` trên WS của trận: mọi message của trận dùng chung bộ đếm `n` và client kiểm `n` liên tục; gửi riêng cho một số client sẽ làm client khác thấy lỗ hổng `n`.
  - Lô 100 ms (≤ 10 lô / s / client), tối đa 40 sự kiện; **sự kiện claim và Narrator không bao giờ bị bỏ**, chỉ cắt sự kiện studio cũ; `recent` giữ theo từng workflow. Hai lỗi này lộ ra khi test: sau một lần phát lại 1000x, hàng đợi studio đẩy mất sự kiện claim khỏi lô và khỏi ảnh chụp.
  - Lần gọi tool (Stats Tools) được đếm vào agent, không phát; span tool kết thúc trước span agent, nên dòng thống kê được tạo ngay từ lần gọi tool đầu (lỗi lộ ra khi test).
  - Narrator: clip dùng chung giữa các phiên nên không gán được span tổng hợp; làn Narrator ghi lúc phiên giao một câu cho Narrator (`cue`), mỗi câu một lần.
  - Trang `/agents?match=`: hai làn (trạng thái bằng biểu tượng + chữ), trạng thái dùng chung, nhật ký chuyển giao bằng lời, bảng agent (vai trò, số lần, p95, nguồn), chi tiết span gần nhất, Chaos ở cạnh phải; link từ header Studio và trang đầu; ở 390 px không cuộn ngang; axe 0 serious / critical.
- **C6 Chaos:** chỉ phiên riêng (403 ở phiên chung, 429 khi quá 10 lần / phút), mỗi lúc một lỗi, tự tắt sau tối đa 5 phút, Reset đưa breaker về nửa mở để thử ngay. Phiên bật Chaos có `ClaimService` / `StudioService` riêng với breaker riêng; test: phiên khác và breaker của app không đổi.
  - "Model nói dối" đổi con số đầu tiên (+3) trong câu của writer; câu sai được nhớ **kèm claim hoặc khoảnh khắc nó được viết cho**. Hai báo động giả được sửa trong lúc test: (1) bộ đếm đọc cả trace (trace giữ bản nháp bị chặn, không bao giờ lên màn hình); (2) cùng một câu có thể sai ở lần phạt góc thứ ba nhưng đúng ở lần thứ sáu.
  - Kết quả: một trận + claim với model nói dối: hơn 100 câu sai, **0 câu lên màn hình**; câu "Trong hiệp 5…" (đổi số hiệp) cũng bị chặn.
  - Studio trên Space viết theo mẫu nên chỉ claim gặp lỗi; bảng Chaos ghi rõ. Lớp bản ghi (C7) chưa có.
- **C9:** dòng trạng thái trang đầu bằng lời thường (model và nơi chạy, câu studio theo mẫu, claim trả lời hai lần, giọng); không còn chữ "mock" trên UI; chip nguồn của claim bằng lời ("Luật · 0,1 s", "Model · qwen2.5:1.5b · trực tiếp · 21 s", lý do khi model bị bỏ qua); HelpDrawer giải thích progressive và nguồn câu chữ; "Thử gì" thêm Agent Ops và Chaos. `/healthz` thêm `llm_live_scope` / `claim_progressive` khi được bật. Trạng thái "model đang khởi động" chưa làm (app chưa biết model đã nạp xong chưa). Bộ ảnh `shots` và demo-run S1–S9 trên Space chờ Space chạy.
- **C11:** `docs/pitch.md`, `docs/video/script.md` (1:55; claim progressive, Agent Ops, Chaos, giọng trình duyệt), `docs/video/voiceover.txt`, `docs/video/subtitles.srt` viết lại theo bản không có Azure; README thêm các tính năng trên. `docs/submission.md` chờ link demo / video.
- **Kiểm thử thêm:** backend `tests/workflow/test_progressive.py` (16, có 100 claim eval), `tests/api/test_progressive_api.py` (5), `tests/api/test_agent_ops.py` (11), `tests/api/test_chaos.py` (17); Vitest `tests/unit/day7.test.ts`, `tests/component/day7.test.tsx`; Playwright project `progressive` PR1–PR4 (backend thật, model giả chậm 1,5 s / lời gọi).
- **Kết quả kiểm thử cuối (27/10, máy dev):** backend 2.648 passed, coverage 95 %, ruff / mypy sạch, eval gate 96 %, 0 verdict sai; `tools` 59 passed; quét secret toàn lịch sử và tuân thủ tên thật sạch; Vitest 202 / 202; Playwright 46 / 46 (e2e, mock, chaos, progressive). Máy dev bị tải nặng trong lúc chạy (bộ backend mất 31 phút thay vì 18), làm vài test thời gian có từ trước lúc qua lúc hỏng (`test_perf_day2` p95, `test_branches_run_concurrently_in_one_superstep`, F7, hai test overlay của project `mock`). So A/B xen kẽ với code trước Day 7 (`b2f135e`) cho thấy chúng hỏng y hệt nhau dưới cùng tải, nên không phải regression. Test của mock overlay được sửa để đo trong cùng một lượt JS / cho đủ thời gian chờ kết nối lại; ba test nặng của Vitest có timeout riêng; một test của C4 vốn phụ thuộc tốc độ được viết lại cho tất định.

### 7.4. M2b, C10, M3, M10 trên Space thật (27/10)

- **M2b:** tài khoản Hugging Face PRO, hai package GHCR (`pundit-copilot`, `pundit-copilot-space`) public, Space **RUNNING** (cpu-basic). Từ đây `space.yml` tự deploy sau mỗi lượt `ci` xanh có thay đổi liên quan; build `3a15622` trả lời trên URL public khoảng 22 phút sau khi push, smoke xanh. Keepalive lớp 1 ping thành công. Khởi động lạnh chưa đo (cần restart Space bằng HF token).
- **M3:** Foundry Local 0.12.1, model `Phi-4-mini-instruct-generic-cpu:5` (bản GPU nạp lỗi "internal error" trên máy này), endpoint OpenAI-compatible `http://127.0.0.1:60877/v1`, gọi qua `LLM_PROVIDER=openai` (cùng client với Foundry cloud). C7 (ghi phiên) chưa làm.
- **C10, eval trên Space** (`tools/ops/measure_space.py eval`, chấm bằng `pundit eval --from`; bảng đầy đủ ở [eval.md](eval.md)):
  - gửi liên tiếp: 96 % trên tập có nhãn, 0 verdict sai, 0 / 270 số bịa; breaker mở nên 97 / 100 lượt model bị bỏ qua;
  - chờ breaker đóng cho mỗi claim (`--wait-model`): 96 %, 0 verdict sai, held-out 68 %, **1 / 239 số bịa** ("giành chiến thắng 2-0" khi đội đó thua 1-2, đã sửa bằng luật `WRONG_OUTCOME`), lượt model p50 39,4 s / p95 54,8 s;
  - Foundry Local, 20 claim có nhãn: 100 %, 0 số bịa, khoảng 330 s / claim trên CPU laptop.
- **Gate S1 trên Space: không đạt** (p95 54,8 s > 45 s). Giữ `qwen2.5:1.5b` vì claim là progressive (rev.1 theo luật đến trước, model chỉ bổ sung) và verdict của model chưa lần nào sai.
- **C10, độ trễ** (`measure_space.py load` / `studio`): rev.1 qua internet với 1 người xem p50 281 ms / p95 485 ms; 3 người xem cùng lúc p50 1.094 / p95 1.156 ms (vượt mục tiêu 1 s một chút vì 2 vCPU bị chia); studio event → item p95 55–62 ms ở 20x trong 4 phút. Event → audio: giọng trình duyệt đọc trên máy người xem, server không tổng hợp clip.
- **Lỗi thật lộ ra khi chạy trên Space, đã sửa và có test:**
  1. janitor dọn phiên dùng chung sau 1 giờ dù phiên đang được dùng qua API (claim không cập nhật `last_change`; chỉ viewer WS mới cập nhật) → `hub.touch()` trong `add_claim`;
  2. Guardrail cho cặp số sau từ chỉ kết quả qua như một cặp chỉ số → `WRONG_OUTCOME` (tỷ số trận, hoặc số bàn theo khung thời gian có trong facts);
  3. lượt model bị Guardrail chặn hết câu bị báo "Model không kịp trả lời" → `skip_reason: rejected` ("Guardrail chặn câu của model…"), rev.2 giữ trace của lượt model để drawer hiển thị bản nháp bị chặn;
  4. hàng đợi talking point trống đặt `<p>` thẳng trong `<ul>` (axe `list`, serious) lúc phiên riêng vừa bắt đầu; test local quét axe sau khi đã có talking point nên không thấy;
  5. (test) state machine phiên riêng thỉnh thoảng báo `FlakyStrategyDefinition` vì token ngẫu nhiên làm đổi thứ tự khoá → token tất định trong test.
- **M10, phần tự động** (`frontend/playwright.space.config.ts`, `tests/e2e-space/`; chạy tay, không chạy trên CI vì phụ thuộc Space thật và giới hạn 3 phiên riêng / 10 phút mỗi IP): Edge 1366 × 768 S1–S9 (có axe ở trang đầu, Fan, Agent Ops), Chrome S1 / S2 / S5, điện thoại giả lập (Pixel 7) trang đầu / Fan bật giọng bằng chạm / Agent Ops không cuộn ngang. Kết quả cuối: xem dòng kiểm thử bên dưới.
- **M10, kết quả tự động trên build `b70913c`:** Edge S1–S9 qua (2,2 phút, gồm rev.2 thật của model và Chaos "model nói dối": 0 số sai lên màn hình); Chrome S1 / S2 / S5 và điện thoại giả lập qua (1,1 phút). Lượt đầu trên build cũ lộ lỗi 4 ở trên. Edge headless không có giọng vi-VN: trang Fan nói thẳng "Trình duyệt này không có giọng tiếng Việt", nên nghe bằng tai phải làm trên thiết bị thật. Kiểm một claim trên Space sau khi sửa: lượt model trả lời sau 34 s, Guardrail chặn, rev.2 `skip_reason: rejected` kèm trace. Còn chờ bạn: điện thoại thật qua 4G, hai trình duyệt / hai phiên riêng với Chaos (test backend `tests/api/test_chaos.py` đã kiểm phiên khác không đổi).
- **Kết quả kiểm thử cuối (27/10):** backend 2.667 passed (2 skip: Azurite, azure-identity), ruff / mypy sạch; `tools` 60 passed; Vitest 202 / 202; Playwright local 46 / 46 (e2e, mock, chaos, progressive); Playwright trên Space 3 / 3; CI `8ac8b30`, `b70913c` xanh, `space` xanh.

### 7.5. M5, UptimeRobot (keepalive lớp 3) (27/10)

- **Cấu hình:** monitor **Keyword** `https://hnam2501-pundit-copilot.hf.space/healthz`, keyword `"status":"ok"`, báo khi keyword **không tồn tại**, interval 5 phút (mức miễn phí), alert qua email của chủ tài khoản. Một monitor HTTP tạo trước đó (chỉ kiểm tra mã trả về của trang chủ) đã xoá để một sự cố không gửi hai email.
- **Đã kiểm chứng (không chỉ cấu hình):**
  1. kiểm tra đầu tiên Up, phản hồi khoảng 359–553 ms từ Nuremberg / Falkenstein (Đức);
  2. Test Notification: email về hộp thư chính;
  3. thử báo động thật: đổi tạm keyword thành `"status":"zzz"` → monitor **Down** lúc 09:18:12 UTC, email "Monitor is DOWN" (root cause "Keyword Does Not Exist");
  4. đổi lại `"status":"ok"` → **Up** lúc 09:19:18 UTC, email "Monitor is UP", incident 1 phút 6 giây.
- **Lưu ý:** uptime 24 giờ trên bảng điều khiển ghi 89,1 % vì incident thử nghiệm này, không phải sự cố của Space. Chuỗi keyword khớp từng ký tự với JSON của `/healthz` (không có khoảng trắng sau dấu `:`); nếu `/healthz` đổi định dạng JSON thì phải sửa keyword cho khớp.
- **Dọn dẹp sau cuộc thi:** pause hoặc xoá monitor này cùng watchdog và token HF (xem §5, mục "Từ 11/11").

### 7.6. M10 trên iPhone thật và C14, giọng bình luận dày và nhanh hơn (28/10)

**M10, phần điện thoại thật (bạn kiểm, iOS Safari, Wi-Fi):** giọng tiếng Việt đọc được, đúng số liệu (sau khi đổi trang Fan sang VI: giọng chỉ đọc bình luận tiếng Việt). Bốn nhận xét:

1. giọng "nam" nghe giống giọng "nữ";
2. giọng đều, thiếu cảm xúc, chậm;
3. chỉ đọc ở tình huống đặc biệt, thiếu chi tiết từng pha;
4. nghe "thuần AI".

**Nguyên nhân (đọc code):**

| Nhận xét | Nguyên nhân |
|---|---|
| 1 | iOS chỉ có một giọng vi-VN (Linh). `pickVoice` tìm `HoaiMy` / `NamMinh` (tên giọng Azure, Edge có bản Online Natural), không thấy thì lấy giọng vi đầu tiên: cả hai lựa chọn rơi về cùng một giọng |
| 2 | Preset Day 5 chênh nhau ít (rate 0 → +16 %), mức `calm` còn giảm volume 15 %; iOS lại co hẹp rate / pitch của giọng hệ thống |
| 3 | `speak()` chỉ đọc mục importance ≥ 2 và vài loại tóm tắt: cú sút, phạt góc, việt vị, VAR, khoảng lặng có chữ nhưng **không bao giờ được đọc**. Mật độ thiết kế Day 3: mốc thống kê 15', khoảng lặng ≤ 5' |
| 4 | Giọng hệ thống của trình duyệt; câu là template (đúng nguyên tắc "không có bằng chứng thì không nói") |

**Đã làm (C14):**

- **Đọc mọi câu:** `speak()` trả `True` cho mọi mục; hồ sơ fan của CLB vẫn tắt tiếng mục `low` như trước. Bộ lập lịch của Fan đã bỏ câu cũ theo `STALE_MS` và ưu tiên câu kịch tính, nên không bị dồn hàng.
- **Pha bóng nhỏ (cue mới):** `tackle` (tắc bóng thắng), `foul` (phạm lỗi, đá phạt cho đội kia), `box_entry` (đường chuyền thành công vào vòng cấm). Chỉ phát khi đã 40 s (`PLAY_GAP_MS`) không có câu nào và sự kiện không sinh moment / cue khác; mỗi cue mang số đếm của đội từ Stats Tools (`tackles`, `fouls`, `box_entries`). Template VI / EN × analyst / casual, ba câu mỗi nhóm cộng bậc dự phòng; không có talking point hay lower-third cho các cue này (importance 1, không thuộc `GRAPHIC_KINDS`).
- **Mật độ:** mốc thống kê mỗi 5' (16 mốc / trận, lower-third vẫn chỉ ở 30' và 75'); khoảng lặng `LULL_MS` 4,5' → 1,75'. Đo bằng `pundit studio --report`: 110–154 mục bình luận / trận trên 5 trận (trước 53–99), khoảng lặng dài nhất 1,8–2,1' (ma trận 12 trận: tối đa 2,8', vì cue khoảng lặng chờ sự kiện kế tiếp); 0 rò rỉ, 0 lỗi oracle, Guardrail không chặn câu template nào. Ngưỡng báo cáo đổi: 100–180 mục, khoảng lặng ≤ 3'.
- **Phạm lỗi:** câu chỉ nhắc đội phạm lỗi cạnh con số; bản đầu nhắc cả hai đội ("đá phạt cho {opp} … ({team}: N lần phạm lỗi)") và test đột biến cho thấy nếu tráo tên hai đội thì Guardrail không bắt được.
- **Nhịp giọng:** preset mới, rate / pitch / volume (%): `calm` +8 / +2 / −5, `lively` +14 / +6 / −3, `excited` +20 / +10 / 0, `peak` +25 / +14 / 0, `burst` +30 / +20 / 0, `tension` −2 / −3 / 0, `recap` +4 / 0 / −8, `greeting` +4 / +2 / −8 (`explainer` giữ chậm hơn). Nghỉ giữa các khoảnh khắc trong recap 400 → 250 ms. Cú sút bị cứu thua / dội cột đọc `lively` (excitement 1); `box_entry` cũng `lively`.
- **Nam / nữ:** Fan chỉ hiện nút chọn khi hai giọng thực sự khác nhau: giọng của server (Azure / mock), hoặc trình duyệt có cả hai giọng (`hasBothGenders`; Edge desktop có). iPhone: ẩn nút, không còn lựa chọn giả.
- **Video demo:** quay trang Fan bằng **Edge desktop** (giọng `Microsoft HoaiMy / NamMinh Online (Natural)` qua đúng Web Speech API), không cần đổi code.

Giá trị mới thay cho các con số tương ứng ở day3-design §4 (mốc 15', khoảng lặng ≤ 5') và bảng preset ở day5-design §6.

**Kiểm thử (Windows chặn mọi `python.exe` trên máy dev từ 28/10, kể cả bản base của uv: backend chạy trong image `pundit-copilot` với mã nguồn mount vào, cộng `pytest` / `hypothesis` / `mypy` cài vào một volume riêng):**

- test mới / sửa: cue nhỏ (đúng loại sự kiện, vai trò, số đếm, cách nhau ≥ 40 s), khoảng lặng ≤ 3', 16 mốc thống kê, mọi câu được đọc và cú sút cứu thua đọc lively, preset (golden SSML, clip id, ước lượng thời lượng theo rate); Guardrail kiểm **mọi** câu template mới như câu của model (`model_text=True`) và bắt được câu bị đột biến (số, cầu thủ, tráo đội);
- eval "Người mới xem": mốc thống kê 5' có thể rơi đúng vào sự kiện VAR (sự kiện đầu tiên sau mốc); test nay bỏ qua dòng theo đồng hồ (`stat_tick`, `lull`) khi tìm "dòng đầu tiên cần giải thích luật", vì dòng đó nói về thống kê;
- backend: agents, speech, workflow, api, contract, golden, cli, unit, property, replay, scenario, mcp, eval, llm, perf: 2.700+ test qua; thêm `-m slow` cho triggers / templates / studio eval / beginner eval (ma trận 12 trận): qua; ruff, mypy sạch. Bỏ qua: `tests/live` (gọi model thật), Playwright (chưa chạy lại; giao diện chỉ đổi nút nam / nữ, có test component);
- frontend: `hasBothGenders` (Edge / iPhone / một giọng / không giọng), nút nam / nữ ẩn trên iPhone và hiện trên Edge; Vitest 2 file liên quan, typecheck, eslint, prettier sạch.

### 7.7. C12 watchdog, README và `docs/submission.md` (28/10)

- **C12 (lớp 2):** M4 (Copilot viết watchdog) quá hạn nên Claude viết theo đúng đặc tả §3.5 (quy tắc §2.12): `tools/ops/watchdog.py` (thư viện chuẩn + `huggingface_hub==2.2.0` chỉ nạp khi chạy thật), `.github/workflows/watchdog.yml` (mỗi 20', `issues: write` + `contents: read`, concurrency không huỷ, 15'), `tools/ops/test_watchdog.py` 17 test (khoẻ → không làm gì; SLEEPING / RUNTIME_ERROR → restart; probe lỗi một lần → thử lại, không restart; lỗi hai lần → restart, không lên → issue + exit 1; issue đang mở → comment, không mở thêm; khoẻ lại → đóng issue; PAUSED → báo, không restart; đang deploy (`RUNNING_APP_STARTING`) mà probe khoẻ → khoẻ; đang khởi động → chờ, không restart; token không bao giờ in ra). Khác đặc tả: dùng `urllib` thay `httpx` (như `smoke_space.py`). `docs/copilot.md` không ghi phần này cho Copilot. Còn bạn: chạy tay một lần (Actions → watchdog → Run workflow).
- **README:** bỏ ghi chú nháp; thêm "Try it in three steps", bảng 5 tiêu chí chấm, giọng đọc dày hơn (C14), ba lớp keepalive; số test cập nhật. Link video điền khi có.
- **`docs/submission.md`:** mọi ô của form (tên, tagline, mô tả 60 từ, pitch, link, hạng mục, testing instructions 5 bước, ghi chú cho giám khảo); chỉ thiếu link video.
- **Không làm:** C7 (ghi phiên Foundry Local cho Studio): máy dev chặn `python.exe`, Studio trên Space dùng template và README nói đúng như vậy.
