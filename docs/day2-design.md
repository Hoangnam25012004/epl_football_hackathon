# Detailed Design — Ngày 2 (22/10): Orchestrator · Claim Verifier · Guardrail · Moment Detector · API

> Liên quan: [claude.md](../claude.md) · [checklist.md](../checklist.md) (mục Ngày 2) · [day1-design.md](day1-design.md) (nền dữ liệu, v1.3)
> Phiên bản: v1.0 (20/10/2026) · Người thực hiện: 1 người · Python 3.12
> Chiến lược: **mock trước, LLM thật sau.** Mọi chức năng chạy và được test đầy đủ với mock. Sau đó gắn GitHub Models (LLM thật tạm thời), rồi Microsoft Foundry khi có Azure, chỉ bằng cách đổi cấu hình.

---

## 0. Mục tiêu, phạm vi và Definition of Done

Ngày 2 xây **bộ não kiểm chứng** của Pundit Copilot: nhận một nhận định (claim), tách thành truy vấn số liệu, tính bằng Stats Tools của Ngày 1, ra phán quyết có bằng chứng, viết lời giải thích, và chặn mọi câu có số liệu sai. Song song là **Moment Detector** phát hiện khoảnh khắc quan trọng, và **API / WebSocket** để dashboard (Ngày 4) dùng.

**Definition of Done có hai tầng**, vì Azure chưa sẵn sàng:

| Tầng | Điều kiện | Kiểm tra bằng |
|---|---|---|
| **A — Mock** (bắt buộc trong Ngày 2) | Gửi claim *"Đội nhà áp đảo hiệp 2"* cho `dominant_loser-42` (tính tới hết trận) → verdict **TRUE**, kèm số liệu: kiểm soát bóng 61% so với 39%, sút 15–1, xG 2.24–0.24, momentum 0.91, cùng danh sách event ID. Toàn bộ test xanh, không cần mạng | `uv run pundit claim "Đội nhà áp đảo hiệp 2" --scenario dominant_loser --seed 42` và `POST /claims/verify` |
| **B — LLM thật** | Cùng claim đó qua **GitHub Models** (ngay khi có token), sau đó qua **Foundry** (khi có Azure), cho ra **cùng verdict, cùng số liệu**. Bộ eval 20 claim: tỷ lệ verdict sai = 0, độ chính xác ≥ 90%, và không có số liệu bịa nào lọt qua Guardrail | `uv run pytest -m live`, `uv run pundit eval --provider github` |

**Ngoài phạm vi Ngày 2** (thuộc Ngày 3–6): Talking-Point Agent, Graphics Agent, Vietnamese Commentator, Personalization, giọng đọc, UI, MCP server, deploy Azure, tạm dừng/tua (pause/seek) phiên phát lại.

---

## 1. Nguyên tắc thiết kế (bổ sung cho Day 1 §1)

| # | Nguyên tắc | Áp dụng |
|---|---|---|
| L1 | **LLM hiểu chữ, code tính số** | LLM chỉ làm 2 việc: (1) dịch câu claim thành `ClaimSpec` có cấu trúc, (2) viết câu giải thích từ các con số đã có. Mọi phép tính và phán quyết là code tất định (deterministic) dùng Stats Tools |
| L2 | **Không chắc thì không phán** (fail-safe) | Không hiểu claim, tên đội/cầu thủ mơ hồ, hoặc cửa sổ thời gian chưa diễn ra → `INSUFFICIENT_DATA` kèm mã lý do. **Tuyệt đối không đoán ra một verdict có thể sai** khi đang lên sóng |
| L3 | **Ghim thời điểm (`as_of_seq`)** | Claim được đánh giá trên một khung nhìn cố định `view(upto=as_of_seq+1)`. Replay chạy tiếp trong lúc kiểm chứng cũng không làm số liệu xê dịch giữa các bước |
| L4 | **Mọi câu chữ đều qua Guardrail** | Kể cả câu từ template. Có test bảo đảm template luôn qua Guardrail, nên lối thoát cuối cùng không bao giờ bị chặn |
| L5 | **Retry có giới hạn, fallback tất định** | Mỗi bước LLM retry tối đa 1 lần, có ngân sách thời gian. Hết lượt thì dùng bản tất định. Số lần thử nằm trong message, không nằm ở biến toàn cục |
| L6 | **Ports & adapters** | Logic agent (`pundit.agents`) không import MAF, FastAPI hay HTTP. MAF chỉ nằm ở `pundit.orchestration` và `pundit.llm.maf`; FastAPI chỉ nằm ở `pundit.api` |
| L7 | **Mock chính là fallback** | Bộ phân tích claim rule-based và bộ giải thích template vừa là mock khi dev/test, vừa là phương án dự phòng khi LLM lỗi. Cùng một code, được test kỹ nhất |
| L8 | **CI không cần mạng, không cần secret** | Test mặc định chạy với mock. Test LLM thật đánh dấu `live`, chỉ chạy khi có biến môi trường |

---

## 2. Kiến trúc

### 2.1. Luồng tổng thể

```
                      ┌────────────────────── MatchSession (1 trận đang phát) ──────────────────────┐
 POST /matches ─────► │ replay(events) ─► MatchState.apply ─► MomentDetector ─► Hub (WS broadcast) │──► WS /ws/match/{id}
                      │        (single writer)        │                 │            ▲                │
                      │                               ▼                 ▼            │                │
                      │                         EventLog / LogView   moments[]   claims[]            │
                      └───────────────────────────────┬──────────────────────────────┬──────────────┘
                                                      │ view(upto=as_of_seq+1)        │ ClaimResult
 POST /claims/verify ──► ClaimService ──► MAF Workflow (mỗi request một lần chạy) ─────┘
                                            │
        ClaimIntake → ClaimAnalyst → EvidenceCollector → Judge → Explainer → Guardrail → ClaimResult
                         │  (LLM | rule)         │ (Stats Tools)      (LLM | template)   │
                         └──► RuleAnalyst        └──► stats.registry                      └──► retry / TemplateExplainer
```

### 2.2. Danh sách agent của Ngày 2

| Agent | Loại | Vào → Ra | LLM? |
|---|---|---|---|
| **ClaimIntake** | Tất định | `ClaimRequest` → `ClaimContext` (ghim `as_of_seq`, nhận diện ngôn ngữ) | Không |
| **ClaimAnalyst** | LLM (GitHub Models / Foundry) hoặc rule-based | `ClaimContext` → `ClaimAnalysis` (`ClaimSpec`) | Có / Không |
| **EvidenceCollector** | Gọi tool | `ClaimAnalysis` → `EvidenceBundle` (các tool call tới Stats Tools) | Không |
| **Judge** | Tất định (rubric) | `EvidenceBundle` → `JudgedClaim` | Không |
| **Explainer** | LLM hoặc template | `JudgedClaim` → `ExplanationDraft` | Có / Không |
| **Guardrail** | Tất định (+ bộ lọc nội dung của Foundry) | `ExplanationDraft` → `GuardrailReport` | Không |
| **MomentDetector** | Rule-based, chạy theo luồng event | `Event` → `Moment[]` | Không |

Cách thiết kế này đáp ứng các tiêu chí của **Best Multi-Agent System**:
- **Vai trò riêng biệt:** mỗi agent làm đúng một việc ở bảng trên.
- **Handoff có kiểu:** agent trao cho nhau các message Pydantic định nghĩa ở mục 3.
- **Shared state:** các agent đọc chung `MatchSession` (khung nhìn ghim theo `as_of_seq`, danh sách moment, nhật ký claim).
- **Phục hồi khi lỗi:** đường fallback hiện rõ trong đồ thị workflow ở mục 9.
- **Trace đầy đủ:** mục 12.

### 2.3. Thư mục mới

```
backend/src/pundit/
├─ agents/                     # logic agent, KHÔNG phụ thuộc MAF/FastAPI
│  ├─ contracts.py             # mọi message có kiểu (mục 3)
│  ├─ language.py              # nhận diện vi/en, chuẩn hoá chữ (NFC, bỏ dấu để so khớp, đ→d)
│  ├─ entities.py              # tìm đội / cầu thủ theo tên, bí danh, "đội nhà/đội khách"
│  ├─ lexicon.py               # từ khoá chỉ số VI/EN → metric, chiều so sánh (polarity)
│  ├─ timescope.py             # "hiệp 2", "15 phút cuối", "từ phút 60" → Window
│  ├─ analyst_rules.py         # ClaimAnalyst rule-based (mock + fallback)
│  ├─ spec_validation.py       # kiểm tra ngữ nghĩa ClaimSpec (dùng chung cho rule & LLM)
│  ├─ collector.py             # ClaimSpec → tool calls → EvidenceBundle
│  ├─ judge.py · rubric.py     # luật phán quyết, rubric "áp đảo", bảng gộp verdict
│  ├─ explain_templates.py     # template VI/EN (mock + fallback)
│  ├─ guardrail.py             # trích số, đối chiếu, bằng chứng, an toàn nội dung
│  ├─ moments.py               # MomentDetector
│  └─ trace.py                 # TraceRecorder, TraceSpan
├─ llm/
│  ├─ ports.py                 # ClaimAnalystPort, ExplainerPort (Protocol)
│  ├─ mock.py                  # MockLLM = rule analyst + template explainer + fault injection
│  ├─ maf_backend.py           # adapter MAF, client tương thích OpenAI (GitHub Models / Foundry)
│  ├─ prompts.py               # prompt + few-shot (có version)
│  └─ settings.py              # đọc biến môi trường, kiểm tra cấu hình khi khởi động
├─ orchestration/
│  ├─ claim_workflow.py        # MAF Workflow: executors + edges (mục 9)
│  ├─ resilience.py            # timeout, retry, circuit breaker, ngân sách thời gian
│  ├─ tools.py                 # catalog Stats Tools dạng function tool (dùng lại cho Ngày 3 và MCP)
│  └─ service.py               # ClaimService.verify(request) -> ClaimResult
└─ api/
   ├─ app.py                   # create_app(settings): FastAPI factory
   ├─ sessions.py              # MatchSession, SessionManager
   ├─ hub.py                   # broadcaster, hàng đợi có giới hạn cho từng client
   ├─ routes.py                # REST
   └─ ws.py                    # WebSocket
```

**Quy tắc import một chiều:** `domain ← stats ← state ← engine ← agents ← llm ← orchestration ← api ← cli`. Có test kiểm tra quy tắc này (quét `import` bằng `ast`), nên vi phạm sẽ làm CI fail.

### 2.4. Thay đổi nhỏ ở code Ngày 1
- `MatchState.view(upto: int | None = None)`: lấy khung nhìn prefix để ghim `as_of_seq`.
- `pundit schema`: xuất thêm `claim_request`, `claim_result`, `moment`, `ws_message`.
- `pundit` CLI: thêm `claim`, `serve`, `eval`, `moments` (mục 11.5).
- `pyproject.toml`: thêm marker `live`; dependency mới ở mục 2.5.

### 2.5. Dependency mới

| Gói | Dùng cho | Ghi chú |
|---|---|---|
| `fastapi`, `uvicorn[standard]` | API + WebSocket | |
| `agent-framework-core`, `agent-framework-openai` | Workflow + client tương thích OpenAI | **Ghim đúng phiên bản sau bước spike** (mục 9.6). API của MAF đã thay đổi giữa các bản: `ChatAgent`/`Agent`, `ai_function`/`@tool`, `model_id`/`model` |
| `azure-identity` | Xác thực Foundry không cần key (Managed Identity / `az login`) | Chỉ thêm khi gắn Foundry |
| `httpx` (dev) | `TestClient` của FastAPI | |

---

## 3. Hợp đồng dữ liệu (`agents/contracts.py`)

Tất cả là Pydantic `frozen=True, extra="forbid"`, có JSON Schema xuất ra `packages/schemas/` (contract test giống Ngày 1).

```python
Lang = Literal["vi", "en"]
Verdict = Literal["TRUE", "PARTLY_TRUE", "FALSE", "INSUFFICIENT_DATA"]
Outcome = Literal["SUPPORTED", "PARTIAL", "CONTRADICTED", "INSUFFICIENT"]
ReasonCode = Literal["ok", "unsupported_claim", "unknown_entity", "ambiguous_entity",
                     "future_window", "no_data_in_window", "low_sample", "as_of_out_of_range"]

class ClaimRequest(BaseModel):          # từ API / CLI
    match_id: str
    text: str = Field(min_length=3, max_length=300)
    language: Literal["auto", "vi", "en"] = "auto"
    as_of_seq: int | None = None        # None = event mới nhất tại lúc nhận request
    include_trace: bool = False

class MatchContext(BaseModel):          # thứ agent được "nhìn thấy" (không có số liệu thống kê)
    match_id: str; as_of_seq: int; period: int; clock_display: str
    teams: list[TeamRef]                # id, name, short_name, side, aliases
    players: list[PlayerRef]            # id, team, name, short_name, aliases (không dấu)
    score: dict[str, int]               # tỷ số tại as_of (được phép nhắc trong lời giải thích)
    metrics: list[MetricRef]            # tên, scope, unit, polarity, mô tả VI/EN (từ REGISTRY)

class Subject(BaseModel):
    kind: Literal["team", "player", "both_teams"]
    team: TeamId | None = None
    player: PlayerId | None = None

class Assertion(BaseModel):
    kind: Literal["dominance", "compare", "value"]
    subject: Subject
    other: Subject | None = None        # compare: mặc định là đối thủ
    metric: str | None = None           # tên trong REGISTRY (compare / value)
    op: Literal[">", ">=", "<", "<=", "==", "~"] | None = None   # "~" = xấp xỉ
    value: float | None = None          # value: con số được nói ra (theo unit của metric)
    ratio: float | None = None          # "gấp đôi" → 2.0
    window: Window                      # MinuteWindow | RollingWindow (Day 1)
    negated: bool = False               # "không áp đảo", "chưa có ..."
    approx: bool = False                # "khoảng", "gần", "around"
    text_span: str                      # đoạn chữ trong claim sinh ra assertion này

class ClaimSpec(BaseModel):
    spec_version: Literal["1"] = "1"
    language: Lang
    assertions: list[Assertion] = Field(max_length=3)   # rỗng = không hiểu claim
    unsupported_reason: ReasonCode | None = None
    notes: list[str] = []               # rule nào đã khớp / LLM giải thích ngắn

class ToolCall(BaseModel):              # ghi lại mọi lần gọi Stats Tools
    tool: str; args: dict[str, Any]; result: StatResult; duration_ms: float

class Fact(BaseModel):                  # một con số được phép nói ra (dùng chung cho Ngày 3)
    key: str                            # "possession:harbor_lions:H2"
    label_vi: str; label_en: str
    value: float; unit: Unit; display: str               # display() của Day 1
    evidence_event_ids: tuple[str, ...]                  # chỉ số = 0 thì dùng "bằng chứng phủ" (mục 5.5)
    derived: bool = False               # hiệu / tỷ lệ do Judge tính từ 2 Fact gốc

class AssertionResult(BaseModel):
    assertion: Assertion; outcome: Outcome; facts: list[Fact]; rationale_code: str

class JudgedClaim(BaseModel):
    context: MatchContext; spec: ClaimSpec
    verdict: Verdict; reason_code: ReasonCode
    results: list[AssertionResult]
    headline: list[Fact]                # 1–4 con số chính, để giải thích và hiển thị

class ExplanationDraft(BaseModel):
    text: str; language: Lang; source: Literal["llm", "template"]; attempt: int

class GuardrailViolation(BaseModel):
    code: Literal["UNVERIFIED_NUMBER", "MISSING_EVIDENCE", "VERDICT_MISMATCH",
                  "UNSAFE_CONTENT", "EMPTY", "TOO_LONG"]
    detail: str; span: str | None = None

class GuardrailReport(BaseModel):
    passed: bool; violations: list[GuardrailViolation]

class ClaimResult(BaseModel):           # trả cho API / UI
    claim_id: str; match_id: str; as_of_seq: int; as_of_display: str
    text: str; language: Lang
    verdict: Verdict; reason_code: ReasonCode
    assertions: list[AssertionResult]; headline: list[Fact]
    explanation: str; explanation_source: Literal["llm", "template"]
    evidence_event_ids: tuple[str, ...]          # hợp của evidence mọi Fact, sắp xếp, không trùng
    guardrail: GuardrailReport                   # của câu giải thích đã công bố
    degraded: list[Literal["analyst_fallback", "explainer_fallback", "circuit_open"]]
    timings_ms: dict[str, float]
    trace: list[TraceSpan] | None = None

class Moment(BaseModel):                # mục 10
    id: str; match_id: str; kind: MomentKind; importance: Literal[1, 2, 3]
    team: TeamId | None; player: PlayerId | None
    period: int; clock_ms: int; display_minute: str
    trigger_event_ids: tuple[str, ...]; facts: list[Fact]; seq: int
```

**Phiên bản hợp đồng:** `ClaimSpec.spec_version`, và `v` trong mọi message WebSocket. Đổi hay xoá trường thì phải nâng version và cập nhật schema cùng test.

---

## 4. Phân tích claim (ClaimAnalyst)

### 4.1. Các loại claim được hỗ trợ

| Loại | Ví dụ VI | Ví dụ EN | `ClaimSpec` |
|---|---|---|---|
| Áp đảo | "Đội nhà áp đảo hiệp 2" | "Harbor dominated the second half" | `dominance(team=HAR, window=H2)` |
| So sánh | "HAR có nhiều cú sút hơn KES trong hiệp 2" | "Kestrel had more corners than Harbor" | `compare(shots, HAR > KES, H2)` |
| Tỷ lệ | "HAR sút gấp đôi KES" | "twice as many shots as" | `compare(shots, ratio=2.0)` |
| Giá trị | "KES chỉ có 1 cú sút trong hiệp 2" | "Harbor have 61% possession" | `value(shots, KES, == 1, H2)` |
| Xấp xỉ | "HAR kiểm soát bóng khoảng 60%" | "around 60% possession" | `value(possession, ~ 60, approx=True)` |
| Bằng 0 | "KES chưa có quả phạt góc nào" | "Kestrel have no corners" | `value(corners, == 0)` |
| Ngang nhau | "Hai đội phạm lỗi ngang nhau" | "Both teams have the same number of fouls" | `compare(fouls, both_teams, ==)` |
| Cầu thủ | "Calder ghi bàn trong hiệp 2" | "Calder has scored" | `value(score, player=Calder, >= 1)` |
| Kết quả | "...nhưng vẫn thua", "Kestrel won" | "won despite lower xG" | `compare(score, FULL, <)`, `compare(xg, FULL, <)` |
| Phủ định | "HAR **không** áp đảo hiệp 2" | "Harbor did **not** dominate" | `negated=True` |
| Nhiều vế | "HAR áp đảo hiệp 2 **nhưng** vẫn thua" | "... **but** lost" | 2 assertion |
| Không hỗ trợ | "Trọng tài thiên vị Harbor" | "The referee is biased" | `assertions=[]`, `unsupported_claim` |

### 4.2. Bộ phân tích rule-based (mock + fallback)

Các bước đều là hàm thuần, có test riêng:

1. **Chuẩn hoá:** đưa về NFC; tạo thêm bản "khoá so khớp" (chữ thường, bỏ dấu, `đ→d`, gộp khoảng trắng). Nhờ vậy "Gia Bao", "gia bảo" và chuỗi NFD đều khớp "Gia Bảo".
2. **Ngôn ngữ:** `vi` nếu có ký tự có dấu tiếng Việt hoặc từ khoá VI ("đội", "hiệp", "cú sút"…); ngược lại là `en`. Nếu request đã chỉ định ngôn ngữ thì dùng theo request.
3. **Thực thể:** tên đầy đủ, `short_name` (HAR), từ khoá tên ("Harbor", "Lions"), "đội nhà/chủ nhà/home side" → đội nhà, "đội khách/away side" → đội khách. Với cầu thủ: họ, tên rút gọn, tên đầy đủ, tên tiếng Việt không dấu.
   - Một từ khớp nhiều cầu thủ → `ambiguous_entity`.
   - Không khớp ai → `unknown_entity`.
   - **Tuyệt đối không đoán.**
4. **Phạm vi thời gian:**
   - "hiệp 1 / first half" → `H1`; "hiệp 2 / second half / 45 phút cuối" → `H2`.
   - "N phút cuối / gần nhất / last N minutes" → `RollingWindow.last(N, as_of_ms)`.
   - "từ phút 60 / since the 60th minute" → `MinuteWindow(start_min=60)`; "phút 60–75" → `MinuteWindow(60, 75)`.
   - "cả trận / so far / overall" hoặc không nói gì → `FULL` (tính tới `as_of`).
5. **Từ điển chỉ số** (`lexicon.py`, VI/EN):

   | Từ khoá | Metric | Polarity |
   |---|---|---|
   | kiểm soát bóng, cầm bóng · possession | `possession` | + |
   | cú sút, dứt điểm · shots | `shots` | + |
   | trúng đích · on target | `shots_on_target` | + |
   | xG, bàn thắng kỳ vọng · expected goals | `xg` | + |
   | bàn, ghi bàn · goals, scored | `score` | + |
   | phạt góc · corners | `corners` | + |
   | phạm lỗi · fouls | `fouls` | + |
   | thẻ · cards | `cards` | + |
   | việt vị · offsides | `offsides` | + |
   | đường chuyền · passes | `passes` | + |
   | chuyền chính xác · pass accuracy | `pass_accuracy` | + |
   | vào vòng cấm · box entries | `box_entries` | + |
   | 1/3 cuối sân · final third | `final_third_entries` | + |
   | pressing, gây áp lực · pressing, PPDA | `ppda` | **−** (PPDA thấp hơn = pressing mạnh hơn) |
   | thế trận · momentum | `momentum` | + |

   Polarity cho biết "mạnh hơn / tốt hơn / better" ứng với `>` hay `<`.
6. **Nhận diện vế:**
   - Áp đảo: "áp đảo", "lấn lướt", "làm chủ thế trận", "vượt trội" · "dominate", "on top", "in control".
   - So sánh: "nhiều/ít … hơn" · "more/fewer … than", "gấp đôi/ba" · "twice/three times".
   - Giá trị: số (chữ số, hoặc chữ số đếm 0–20 đi kèm danh từ chỉ số), "%", "chỉ", "chưa có … nào" · "only", "no".
   - Kết quả trận: "thắng/thua/hoà" · "won/lost/drew".
   - Xấp xỉ: "khoảng/gần/xấp xỉ" · "around/about/nearly".
7. **Phủ định:** "không/chưa/chẳng" · "not/never/didn't" đứng trước động từ của vế → `negated=True`. Riêng "chưa có … nào" / "no …" được đọc là `value == 0` (không phải phủ định).
8. **Nhiều vế:** tách theo "nhưng / và / , còn" · "but / and / while" khi mỗi phần có chỉ số hoặc vế áp đảo riêng. Tối đa 3 vế, phần thừa → `unsupported_claim`.
9. **Kiểm tra ngữ nghĩa** (mục 4.3). Nếu không tạo được vế nào → `unsupported_claim`.

### 4.3. Kiểm tra ngữ nghĩa `ClaimSpec` (dùng chung cho rule-based và LLM)

`validate_spec(spec, ctx) -> list[str]` (rỗng là hợp lệ):
- Metric có trong `REGISTRY`, và `scope` của metric khớp với subject (ví dụ `possession` không dùng cho cầu thủ).
- Team / player tồn tại trong trận; cầu thủ thuộc đúng đội nếu có nói đội.
- Window hợp lệ và **không nằm hoàn toàn trong tương lai**. Ví dụ hỏi "hiệp 2" ở phút 30 → `future_window`.
- `compare` phải có 2 subject khác nhau; `ratio > 1`; `value` phải có `op` và `value`; `dominance` chỉ dùng cho team.
- Lỗi từ LLM được gửi lại cho LLM trong lần retry (mục 9.3).

### 4.4. Hợp đồng prompt cho LLM ClaimAnalyst (`llm/prompts.py`, có version)

- **System prompt:** vai trò, các luật ở 4.1–4.3, "chỉ trả JSON đúng schema `ClaimSpec`", "không tự suy ra số liệu", "không chắc thì để `assertions=[]` kèm lý do".
- **Context:** `MatchContext` dạng JSON (đội, cầu thủ, bí danh, danh mục metric, phút hiện tại). Danh mục này nhỏ (khoảng 1–2k token), nên **chỉ cần 1 lần gọi LLM, không cần vòng tool** để tìm thực thể. Cách này nhanh và ổn định hơn.
- **Few-shot:** 6 ví dụ VI/EN, gồm phủ định, nhiều vế, `unsupported`.
- **Output:** structured output với `response_format = ClaimSpec`. Nếu model không hỗ trợ thì dùng JSON mode rồi validate bằng Pydantic. Kết quả luôn qua `validate_spec`.

---

## 5. Judge — phán quyết tất định (`agents/judge.py`, `agents/rubric.py`)

### 5.1. Đánh giá từng vế

Mọi con số lấy từ `stats.compute(view_as_of, metric, params)`, qua EvidenceCollector, và đều được ghi thành `ToolCall`.

| Loại | Luật | Outcome |
|---|---|---|
| `value` | So giá trị thực với giá trị được nói, theo `op` và dung sai (bảng 5.2) | Trong dung sai → SUPPORTED; lệch không quá 2 lần dung sai (hoặc đếm lệch 1 khi giá trị ≥ 5) → PARTIAL; còn lại → CONTRADICTED |
| `compare` | So 2 giá trị theo `op` và polarity. `==` dùng dung sai của unit. `ratio`: thực ≥ 0.9 × tỷ lệ được nói → SUPPORTED; thực > 1 nhưng chưa đạt → PARTIAL (*"nhiều hơn nhưng không gấp đôi"*) | |
| `dominance` | Rubric mục 5.3 | |
| Mọi loại | `StatResult.status == insufficient_data`, hoặc cửa sổ không có thời gian bóng lăn → INSUFFICIENT | |

### 5.2. Dung sai theo đơn vị

| Unit | Nói chính xác | Nói xấp xỉ ("khoảng") |
|---|---|---|
| percent | ± 2.5 điểm | ± 5 điểm |
| count | 0 | ± 1 (khi giá trị ≥ 5) |
| xg | ± 0.05 | ± 0.15 |
| minutes | ± 0.5 | ± 1.5 |
| kmh · meters | ± 1 | ± 3 |
| ratio · index · rating | ± 0.05 | ± 0.15 |

### 5.3. Rubric "áp đảo" (ghi thành hằng số, hiệu chỉnh ở bước 4 của mục 14)

Tính trong cửa sổ của vế, cho đội được nói tới so với đối thủ:

| Chỉ báo | Đạt khi |
|---|---|
| Kiểm soát bóng | `possession ≥ 55%` |
| Cú sút | `shots ≥ 1.5 × đối thủ` **và** `shots − đối thủ ≥ 2` |
| xG | `xg − xg đối thủ ≥ 0.3` |
| Momentum | `momentum ≥ 0.3` |

- `k` = số chỉ báo đạt. `k ≥ 3` → SUPPORTED; `k = 2` → PARTIAL; `k ≤ 1` → CONTRADICTED.
- Nếu thời gian bóng lăn trong cửa sổ dưới 5 phút → INSUFFICIENT (`low_sample`).
- Cả 4 chỉ báo (của cả 2 đội) đều được đưa vào `facts`, để lời giải thích nói đúng con số.
- **Kỳ vọng hiệu chỉnh:**
  - `dominant_loser` H2: HAR đạt 4/4, KES 0/4.
  - `red_card_swing` từ phút 55: HAR đạt ≥ 3.
  - Các hiệp của trận `balanced`: phân bố hợp lý. Kết quả ghi vào bảng của tài liệu sau khi đo.

### 5.4. Gộp thành verdict

Phủ định được áp dụng trước: SUPPORTED ↔ CONTRADICTED; PARTIAL và INSUFFICIENT giữ nguyên.

| Outcome của các vế | Verdict |
|---|---|
| Tất cả SUPPORTED | `TRUE` |
| Tất cả CONTRADICTED | `FALSE` |
| Tất cả INSUFFICIENT, hoặc spec rỗng | `INSUFFICIENT_DATA` (kèm `reason_code`) |
| Mọi trường hợp còn lại | `PARTLY_TRUE` |

Bảng này được test **vét cạn** mọi tổ hợp của 1–3 vế (4³ + 4² + 4 = 84 trường hợp).

### 5.5. Fact và bằng chứng
- Mỗi `Fact` mang `evidence_event_ids` của `StatResult` tương ứng.
- **Chỉ số bằng 0** (ví dụ 0 quả phạt góc) không có event nào làm bằng chứng. Khi đó dùng **bằng chứng phủ**: các event mở đầu spell trong cửa sổ (hàm `spell_starts` của Ngày 1), chứng minh cửa sổ đó thật sự có bóng lăn. Nhờ vậy mọi verdict khác `INSUFFICIENT_DATA` đều có ít nhất 1 event ID.
- Với `compare` và `dominance`, Judge thêm **Fact phái sinh** (hiệu số, tỷ lệ) có `derived=True`, ví dụ "15 so với 1". Nhờ đó lời giải thích được phép nói "gấp 15 lần" mà vẫn qua Guardrail.
- `headline`: tối đa 4 Fact chính theo thứ tự ưu tiên của từng loại vế.

---

## 6. Explainer

### 6.1. Template (mock + fallback, `explain_templates.py`)
- Có template cho mỗi tổ hợp (verdict × loại vế × ngôn ngữ). Câu đầy đủ, ngắn, chỉ dùng `Fact.display`. Ví dụ:
  - **VI · TRUE · dominance:** *"Trong hiệp 2, Harbor Lions FC kiểm soát bóng 61%, dứt điểm 15 lần so với 1 của Kestrel Athletic, xG 2.24 so với 0.24."*
  - **EN · FALSE · compare:** *"Kestrel Athletic had 0 corners in the second half, against 7 for Harbor Lions FC."*
  - **VI · INSUFFICIENT · future_window:** *"Hiệp 2 chưa bắt đầu (hiện tại là phút 30), chưa thể đánh giá."*
- **Định dạng số:** dùng `display()` của Ngày 1; số thập phân dùng dấu chấm ở cả hai ngôn ngữ (xG 2.24), đúng thói quen đồ hoạ bóng đá. Câu giải thích **không chứa nhãn verdict** (UI hiển thị verdict riêng).
- `INSUFFICIENT_DATA` luôn dùng template (không tốn lần gọi LLM).

### 6.2. Hợp đồng prompt cho LLM Explainer
- **Vào:** claim, verdict, các `Fact` (nhãn + `display`), ngôn ngữ, persona (`analyst` là mặc định; `casual` để dành Ngày 3).
- **Luật:**
  - 1–2 câu, tối đa 45 từ.
  - **Chỉ dùng các con số có trong danh sách Fact, viết bằng chữ số.**
  - Không nhắc phút nào ngoài phạm vi của cửa sổ.
  - Không dùng nhãn verdict, không suy đoán, giọng trung lập, không xúc phạm.
- **Ra:** `{"text": "..."}`. Guardrail tự trích số từ `text`, không tin danh sách số do model tự báo.

---

## 7. Guardrail (`agents/guardrail.py`) — dùng lại ở Ngày 3

API dùng chung: `check(text, facts, language, context) -> GuardrailReport`.

### 7.1. Các bước kiểm tra

| Mã | Kiểm tra |
|---|---|
| `EMPTY` / `TOO_LONG` | Văn bản rỗng, hoặc dài quá 300 ký tự |
| `UNVERIFIED_NUMBER` | Mọi con số trích được (mục 7.2) phải khớp một Fact, hoặc nằm trong danh sách ngữ cảnh được phép (mục 7.3) |
| `MISSING_EVIDENCE` | Verdict khác `INSUFFICIENT_DATA` thì mọi Fact được dùng phải có ít nhất 1 event ID |
| `VERDICT_MISMATCH` | Văn bản chứa nhãn verdict (VI: "đúng", "sai", "đúng một phần", "không đủ dữ liệu"; EN: "true", "false", "partly", "insufficient"). So khớp cụm dài nhất trước |
| `UNSAFE_CONTENT` | Danh sách từ cấm VI/EN (xúc phạm, phân biệt đối xử), cộng với tín hiệu bộ lọc nội dung của Foundry (`content_filter`) |

### 7.2. Trích số và đối chiếu
- **Mẫu trích:** số nguyên, số thập phân (`2.24`, và cả `2,24` khi có đúng 1–2 chữ số sau dấu phẩy và không có khoảng trắng), phần trăm (`61%`, "61 phần trăm"), tỷ số `1-2` / `1–2`, phút (`63'`, "phút 63"), và **chữ số đếm 0–20 khi đứng ngay trước một danh từ chỉ số** ("hai cú sút", "three corners").
  - Chữ "không" **không** được tính là số 0, vì trong tiếng Việt nó chủ yếu mang nghĩa phủ định.
  - "hai đội" không bị tính, vì "đội" không phải danh từ chỉ số.
- **Luật khớp "làm tròn nhất quán":** một số viết với `d` chữ số thập phân khớp giá trị thật `v` khi `|viết − v| ≤ 0.5 × 10^(−d) + 1e−9`.
  - Ví dụ: "61%" khớp 61.29; "62%" thì không. "2.2" khớp 2.24; "2.3" thì không.
  - Luật này đúng cho mọi đơn vị, không cần dung sai riêng từng loại.

### 7.3. Ngữ cảnh được phép
- Số hiệp sau "hiệp / half" (1, 2).
- Các mốc của cửa sổ (60, 75, "10 phút gần nhất").
- Phút hiện tại tại `as_of`.
- Tỷ số tại `as_of` (cả hai số).
- Các Fact phái sinh (hiệu số, tỷ lệ).

### 7.4. Kết quả và phản hồi
- Khi không qua, Guardrail trả danh sách vi phạm kèm đoạn chữ gây lỗi. Danh sách này được đưa vào lần retry của Explainer, ví dụ *"Bạn dùng số 65% nhưng dữ liệu là 61%"*.
- Retry tối đa 1 lần, sau đó dùng template.
- Template **cũng phải qua Guardrail**. Nếu template trượt thì đó là bug: test sẽ bắt, còn khi chạy thật thì vẫn trả verdict nhưng không kèm câu giải thích, và đánh dấu lỗi.

---

## 8. Tầng LLM: mock → GitHub Models → Foundry

### 8.1. Ports (`llm/ports.py`)

```python
class ClaimAnalystPort(Protocol):
    async def analyze(self, ctx: ClaimContext, feedback: list[str] | None = None) -> ClaimSpec: ...

class ExplainerPort(Protocol):
    async def explain(self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None) -> ExplanationDraft: ...
```

| Cấu hình | ClaimAnalyst | Explainer |
|---|---|---|
| `USE_MOCK_LLM=true` (mặc định, CI) | `RuleClaimAnalyst` | `TemplateExplainer` |
| `LLM_PROVIDER=github` | `LlmClaimAnalyst(GitHubModelsBackend)` | `LlmExplainer(...)` |
| `LLM_PROVIDER=foundry` | `LlmClaimAnalyst(FoundryBackend)` | `LlmExplainer(...)` |
| Fallback (luôn có) | `RuleClaimAnalyst` | `TemplateExplainer` |

### 8.2. Mock có bơm lỗi (fault injection)

Biến `MOCK_LLM_FAULTS` (hoặc tham số trong test) giả lập hành vi xấu của LLM một cách tất định, ví dụ `analyst:timeout:1,explainer:hallucinate:1`. Các kiểu lỗi:
- `timeout`: chờ quá thời gian cho phép.
- `error`: ném exception.
- `invalid_json`: trả JSON sai schema.
- `wrong_entity`: đổi đội trong spec.
- `hallucinate`: chèn một con số sai vào câu giải thích.
- `unsafe`: chèn từ cấm.

Số đi kèm là số lần lỗi trước khi chạy bình thường. Dùng cho test ma trận lỗi (mục 13.4), và để **demo trong video** cảnh Guardrail chặn số liệu bịa.

### 8.3. Adapter LLM thật qua MAF (`llm/maf_backend.py`)

- Một adapter dùng client tương thích OpenAI của MAF, với `base_url` theo provider:

  | Provider | Endpoint | Xác thực | Model |
  |---|---|---|---|
  | GitHub Models | `https://models.github.ai/inference` (cần xác minh lại ở spike) | `GITHUB_TOKEN` (token có quyền GitHub Models) | Một model "mini" có structured output, ví dụ họ `gpt-4.1-mini` hoặc `gpt-4o-mini`; chọn theo catalog lúc spike |
  | Foundry | `https://<resource>.openai.azure.com/openai/v1` | Entra ID (`DefaultAzureCredential`); API key chỉ dùng khi dev | Tên deployment trong Foundry |

- **Lưu ý về GitHub Models:** đang ở public preview, giới hạn khoảng 150 request/ngày cho model nhỏ và 8k token đầu vào mỗi request (số liệu từ nguồn bên thứ ba). Vì vậy chỉ dùng để thử prompt và chạy eval, **không** chạy trong CI.
- **Structured output:** ưu tiên `response_format` (Pydantic). Nếu không được thì dùng JSON mode, bỏ code fence, rồi parse. Cuối cùng luôn qua `validate_spec`.
- Mỗi lần gọi có timeout riêng. Không tự retry ở tầng HTTP; retry do `resilience.py` quyết định để tránh nhân đôi số lần thử.

### 8.4. Biến môi trường (`.env.example` chỉ chứa tên biến)

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `USE_MOCK_LLM` | `true` | `true` thì bỏ qua mọi cấu hình provider |
| `LLM_PROVIDER` | — | `github` \| `foundry` |
| `GITHUB_TOKEN`, `GITHUB_MODELS_ENDPOINT`, `GITHUB_MODELS_MODEL` | — | Cho provider `github` |
| `FOUNDRY_ENDPOINT`, `FOUNDRY_MODEL_DEPLOYMENT` | — | Theo claude.md §6.3 |
| `LLM_TIMEOUT_S` · `CLAIM_BUDGET_S` | `4` · `8` | Thời gian tối đa cho mỗi lần gọi LLM · cho cả một claim |
| `MOCK_LLM_FAULTS`, `MOCK_LLM_LATENCY_MS` | — | Test / demo |
| `CORS_ORIGINS` | `http://localhost:3000` | Cho frontend Ngày 4 |

Khi khởi động, nếu `USE_MOCK_LLM=false` mà thiếu biến bắt buộc thì **dừng ngay với thông báo rõ ràng**, không chạy nửa vời. Token và key không bao giờ được ghi vào log hay trace.

### 8.5. Danh sách việc khi gắn Foundry (ngay khi có Azure, khoảng 1 giờ)
1. Tạo Foundry resource và deploy một model mini; ghi region và model vào claude.md §8.
2. Đặt `LLM_PROVIDER=foundry` cùng các biến Foundry; chạy `az login` khi dev.
3. `uv run pytest -m live --provider foundry` và `uv run pundit eval --provider foundry`.
4. So sánh với GitHub Models: verdict phải giống nhau trên bộ eval; ghi độ trễ p50/p95.
5. Kiểm tra tín hiệu `content_filter` được map đúng sang `UNSAFE_CONTENT`.

---

## 9. Orchestration trên Microsoft Agent Framework

### 9.1. Đồ thị workflow kiểm chứng claim

```
ClaimIntake ─► ClaimAnalyst ──ok──────────────────────► EvidenceCollector ─► Judge ─► Explainer ─► Guardrail ──pass──► [output ClaimResult]
                    │                                         ▲                        ▲            │
                    └──fail (timeout/invalid sau retry)──► RuleAnalyst                 │            ├─fail & attempt<2──► (về Explainer kèm feedback)
                                                                                       │            └─fail & attempt≥2──► TemplateExplainer ─► Guardrail
                                                     verdict INSUFFICIENT_DATA ────────┴──► TemplateExplainer (bỏ qua LLM)
```

- Mỗi khối là một **Executor**; mỗi cạnh mang **một kiểu message** của mục 3. Việc rẽ nhánh dùng cạnh có điều kiện theo kiểu và nội dung message.
- Số lần thử (`attempt`) nằm trong message, nên vòng lặp Guardrail → Explainer **bị chặn trên bởi kiểu dữ liệu** và không thể chạy vô hạn.
- MAF hỗ trợ vẽ đồ thị workflow (Mermaid), dùng được cho README và video.

### 9.2. Ngân sách thời gian và khả năng phục hồi (`orchestration/resilience.py`)

| Bước | Timeout | Retry | Fallback |
|---|---|---|---|
| ClaimAnalyst (LLM) | 4 s | 1 lần, **chỉ** khi output sai schema/ngữ nghĩa (kèm danh sách lỗi); timeout thì không retry | RuleAnalyst |
| Explainer (LLM) | 4 s | 1 lần khi Guardrail trượt (kèm vi phạm) | TemplateExplainer |
| Cả claim | 8 s | — | Phần còn lại dùng bản tất định |

- **Circuit breaker:** sau 3 lỗi LLM liên tiếp thì mở mạch trong 60 s. Trong lúc mở, mọi claim dùng bản tất định ngay, có cờ `degraded=["circuit_open"]`. Hết 60 s thì cho một lần thử; thành công thì đóng lại. Đồng hồ được inject vào để test tất định.
- **Verdict không bao giờ phụ thuộc Explainer:** LLM giải thích hỏng chỉ ảnh hưởng câu chữ, không đổi phán quyết.

### 9.3. Đồng thời (concurrency) và trạng thái chia sẻ
- **Tạo một workflow mới cho mỗi request** (hoặc dùng lại workflow nếu spike xác nhận an toàn khi chạy song song). Executor không giữ trạng thái giữa các lần chạy.
- `ClaimIntake` ghim `as_of_seq` ngay khi nhận request; mọi bước dùng `session.view_at(as_of_seq)`, là khung nhìn bất biến của Ngày 1.
- Shared state gồm `MatchSession` (một writer duy nhất là task replay; nhiều reader đọc qua `LogView`), danh sách moment và nhật ký claim. Agent của Ngày 3 cũng đọc từ đây.

### 9.4. Catalog Stats Tools (`orchestration/tools.py`)
- Sinh từ `REGISTRY` của Ngày 1: tool `get_stat(metric: enum[29 tên], team?, player?, window)` và `compare_stat(metric, window)`. Schema JSON lấy từ `MetricParams`.
- Dùng bởi: EvidenceCollector (gọi qua cùng lớp bọc nên trace ghi giống hệt), agent của Ngày 3, và MCP server Ngày 6. **Một catalog duy nhất.**
- Kết quả trả về cho LLM bị cắt bớt: evidence chỉ gửi tối đa 10 ID kèm tổng số, để tiết kiệm token. Bản đầy đủ vẫn nằm trong `ClaimResult`.

### 9.5. Bước spike (bước 0 của mục 14, tối đa 1 giờ)

Ghim phiên bản `agent-framework-*`, rồi viết một script nhỏ (không commit vào `src`) để xác minh:
1. Executor có handler nhận/gửi message Pydantic.
2. Cạnh có điều kiện theo kiểu message.
3. Vòng lặp có điều kiện dừng.
4. Chạy 10 workflow song song không lẫn trạng thái.
5. Exception trong executor được trả ra thế nào.
6. Stream sự kiện của workflow (có thể dùng để tự sinh trace).
7. Chạy hoàn toàn offline.
8. Nếu đã có token: client tương thích OpenAI gọi GitHub Models với `response_format`.

Kết quả ghi vào mục này (tên lớp, chữ ký hàm thật).

**Phương án dự phòng:** nếu spike thất bại (API không ổn định hoặc lỗi trên Python 3.12), dùng một runner `asyncio` nhỏ (khoảng 80 dòng) với cùng các message và cạnh. MAF khi đó chỉ còn được dùng làm client LLM, và vẫn là hero technology. Logic agent không phải sửa vì không phụ thuộc MAF (L6).

---

## 10. Moment Detector (`agents/moments.py`)

Chạy trong task replay, ngay sau `MatchState.apply(event)`. Detector có trạng thái nhưng **tất định**: cùng chuỗi event thì ra cùng danh sách moment.

| Kind | Kích hoạt | Mức độ | Ghi chú |
|---|---|---|---|
| `goal` | `GoalEvent` | 3 | Fact: tỷ số sau bàn, người ghi bàn, kiến tạo, xG của cú sút |
| `goal_overturned` | `var_check` overturned | 3 | Rút lại moment `goal` trước đó (trỏ `related` tới nó) |
| `red_card` | Thẻ `red` / `second_yellow` | 3 | |
| `yellow_card` | Thẻ `yellow` | 1 | |
| `big_chance` | Cú sút không thành bàn với `xg ≥ 0.30` | 2 | |
| `rocket_shot` | Cú sút có `speed_kmh ≥ 100` | 1 | Theo ngưỡng của Ngày 1 |
| `dangerous_spell` | Trong một spell của một đội: ≥ 2 cú sút, hoặc ≥ 3 lần vào vòng cấm trong 90 s | 2 | Cooldown 3 phút cho mỗi đội |
| `momentum_shift` | Momentum 10' (góc nhìn đội nhà) đi từ ≤ 0 lên ≥ +0.5, hoặc từ ≥ 0 xuống ≤ −0.5 | 2 | Chỉ tính ở mỗi mốc 1 phút thi đấu (không tính theo từng event); cooldown 5 phút |
| `substitution` | Thay người | 1 | |
| `period_start` · `period_end` · `full_time` | Sự kiện điều khiển trận | 1–2 | Dùng cho recap ở Ngày 3 |

- **ID:** `f"{match_id}-m{n:04d}"`, đánh số theo thứ tự phát hiện, nên tất định.
- Moment chỉ chứa **Fact** (cùng kiểu với Claim Verifier), để Ngày 3 sinh talking point từ đó mà vẫn qua Guardrail.
- `detect_all(events)` (chạy theo lô) và chạy theo luồng phải cho kết quả giống hệt nhau (có test).

---

## 11. Match session, API và WebSocket

### 11.1. `MatchSession`
- Tạo từ `scenario + seed` (sinh tất định) hoặc từ file `data/matches/*.jsonl`.
- Có các tham số `speed`, `from_minute`, và `clock` (inject được; test dùng `FakeClock`).
- **Trạng thái:** `created → running → finished` (hoặc `stopped`, `error`). Lỗi trong task replay được ghi log và phát `status: error`, **không làm sập server**.
- **Dữ liệu:** `state: MatchState`, `moments: list[Moment]`, `claims: OrderedDict` (giữ tối đa 200 claim), `hub: Hub`.
- `view_at(seq)`: lấy prefix bất biến; báo lỗi `as_of_out_of_range` nếu `seq` vượt quá event đã áp dụng.
- Khi tắt app: huỷ mọi task và chờ chúng kết thúc (có test chống rò rỉ task).

### 11.2. REST

| Method | Đường dẫn | Mô tả |
|---|---|---|
| `POST` | `/matches` | `{scenario, seed, speed=20, from_minute=0, autostart=true}` → `MatchInfo`. Idempotent: trận đã chạy thì trả về phiên hiện có, trừ khi gửi `restart=true` |
| `POST` | `/matches/{id}/start` · `/stop` | Điều khiển phiên phát |
| `GET` | `/matches/{id}` | `MatchSnapshot` (của Ngày 1) cùng trạng thái phiên |
| `GET` | `/matches/{id}/moments?since=` | Danh sách moment |
| `POST` | `/claims/verify` | `ClaimRequest` → `ClaimResult` (đồng bộ). Claim cũng được phát qua WebSocket tới dashboard |
| `GET` | `/claims/{claim_id}` | `ClaimResult` kèm trace |
| `GET` | `/healthz` | `{status, llm_provider, degraded, sessions}` |

**Mã lỗi:** `404` (trận/claim không tồn tại), `422` (dữ liệu sai). Lỗi của LLM **không bao giờ** trả `5xx`: luôn có fallback và vẫn trả `200` kèm cờ `degraded`.

### 11.3. Giao thức WebSocket `/ws/match/{id}`

Mọi message có dạng `{v: 1, type, n, match_id, payload}`. Trong đó `n` tăng dần theo từng phiên, để client phát hiện mất hoặc trùng message.

| `type` | Khi nào | `payload` |
|---|---|---|
| `hello` | Ngay khi kết nối | meta tóm tắt, snapshot, 50 moment gần nhất, 20 claim gần nhất (client vào muộn vẫn đủ trạng thái) |
| `event` | Mỗi event (trừ `player_physical`) | `Event` |
| `snapshot` | Mỗi 30 s **thời gian trận** và sau goal / card / hết hiệp | `MatchSnapshot` (cách throttle này tất định, không phụ thuộc tốc độ phát) |
| `moment` | Khi phát hiện moment | `Moment` |
| `claim` | Khi có claim được kiểm chứng | `ClaimResult` (không kèm trace) |
| `status` | Đổi trạng thái phiên, hoặc LLM chuyển sang chế độ degraded | `{session, degraded}` |
| `resync` | Hàng đợi của client bị đầy | snapshot cùng moment gần nhất |

- **Backpressure:** mỗi client có một `asyncio.Queue(maxsize=1000)`. Đầy thì xoá hàng đợi rồi gửi `resync`. **Client chậm không bao giờ chặn replay hay các client khác.**
- **Thứ tự:** Hub phát đúng thứ tự các event đã được `apply`; phát ra một event chưa `apply` là lỗi.

### 11.4. CLI mới
- `pundit claim "<text>" --scenario S --seed N [--at "63'"] [--lang auto] [--trace]`: in verdict, Fact, câu giải thích và tóm tắt trace.
- `pundit serve [--host] [--port]`: chạy uvicorn với `create_app()`.
- `pundit eval [--file] [--provider mock|github|foundry]`: chạy bộ eval, in báo cáo (mục 13.6).
- `pundit moments --scenario S --seed N`: in danh sách moment của cả trận.

---

## 12. Agent Trace (`agents/trace.py`)

```python
class TraceSpan(BaseModel):
    trace_id: str            # = claim_id
    span_id: str             # f"{claim_id}-s{k:02d}", tất định
    parent_id: str | None
    agent: str               # "ClaimAnalyst", "EvidenceCollector", ...
    kind: Literal["agent", "tool", "llm", "guardrail", "fallback"]
    name: str                # ví dụ tên tool "get_stat"
    input: dict[str, Any]    # tóm tắt, cắt ngắn tối đa 2 KB
    output: dict[str, Any]
    status: Literal["ok", "error", "retry", "fallback", "rejected"]
    attempt: int
    start_ms: float          # tính từ lúc bắt đầu claim (đồng hồ monotonic)
    duration_ms: float
    model: str | None        # "mock" | tên deployment
    error: str | None
```

- Thời gian dùng đồng hồ **monotonic tương đối** (không dùng giờ hệ thống), đúng với luật cấm `datetime.now` / `time.time` của Ngày 1.
- Lưu trong bộ nhớ theo phiên. Ghi ra log dạng JSON có cấu trúc. Ngày 5 nối sang OpenTelemetry / Application Insights (MAF có sẵn phần đo OTel).
- **Che thông tin nhạy cảm (redaction):** không ghi header, token hay key. Prompt chỉ ghi `prompt_version` và độ dài, không ghi toàn văn (có cờ debug khi cần).

---

## 13. Chiến lược test

### 13.1. Các tầng test

| Tầng | Thư mục | Nội dung | Ước lượng |
|---|---|---|---|
| Unit | `tests/unit/agents/` | language, entities, lexicon, timescope, analyst_rules, spec_validation, judge, rubric, templates, guardrail, moments, trace, resilience | ~180 |
| Contract | `tests/contract/` | Schema mới đã commit khớp code; ví dụ hợp lệ qua jsonschema | ~6 |
| Property / metamorphic | `tests/property/` | Mục 13.3 | ~25 |
| Workflow | `tests/workflow/` | Workflow MAF với mock: luồng chuẩn, ma trận lỗi, circuit breaker | ~40 |
| API | `tests/api/` | REST + WebSocket qua `TestClient`, `FakeClock` | ~25 |
| Eval | `tests/eval/` | 20 claim + bộ held-out (mục 13.6) | ~4 |
| Live (opt-in) | `tests/live/` | GitHub Models / Foundry, chỉ chạy với `-m live` | ~5 |
| Perf | `tests/perf/` | Ngân sách thời gian (mục 13.8) | ~4 |

Toàn bộ test mặc định chạy trong **dưới 90 s**, không cần mạng.

### 13.2. Các ca bắt buộc theo module
- **language / entities:**
  - Chuỗi đầu vào dạng NFD, không dấu, chữ hoa, có dấu câu vẫn khớp đúng.
  - "đội nhà / đội khách" ra đúng đội.
  - Họ trùng nhau → `ambiguous_entity`; tên lạ → `unknown_entity`.
- **timescope:**
  - Bảng ≥ 20 cách diễn đạt VI/EN.
  - Cửa sổ ở tương lai → `future_window`.
  - Cửa sổ trượt tại `as_of` ra đúng mốc thời gian.
- **analyst_rules:**
  - Bảng ≥ 60 claim (VI/EN, diễn đạt khác nhau, phủ định, nhiều vế, "chưa có … nào", "gấp đôi", "khoảng").
  - Fuzz bằng Hypothesis: chuỗi ngẫu nhiên → **không bao giờ crash**; output luôn hợp lệ (spec rỗng hoặc qua `validate_spec`).
- **judge / rubric:**
  - Dữ liệu dựng tay bằng `MatchBuilder` cho từng loại vế.
  - Biên dung sai: 57.5 so với claim 60 (exact và approx); đếm lệch 1; tỷ lệ 1.9 so với "gấp đôi".
  - Phủ định; chỉ số = 0 có bằng chứng phủ.
  - Bảng gộp verdict vét cạn 84 tổ hợp.
- **guardrail:**
  - Bảng trích số (chữ số, dấu chấm/phẩy, %, tỷ số, phút, chữ số đếm + danh từ, "không", "hai đội", "hiệp 2").
  - **Mutation test:** lấy mọi câu template hợp lệ, đổi từng con số một → phải bị `UNVERIFIED_NUMBER`.
  - Nhãn verdict sai; từ cấm; rỗng / quá dài.
- **templates:** mọi tổ hợp (verdict × loại × ngôn ngữ) đều render được và **qua Guardrail**. Kiểm tra thêm bằng property trên các claim sinh ngẫu nhiên.
- **moments:**
  - Từng luật trên dữ liệu dựng tay.
  - Chạy theo lô = chạy theo luồng (nhiều seed).
  - `dominant_loser-42` có đúng 3 moment `goal` ở đúng phút kịch bản.
  - `comeback_var` có `goal_overturned` trỏ về đúng bàn bị huỷ.
  - `red_card_swing` có `red_card` ở 52'+.
  - Cooldown hoạt động; ID không trùng.

### 13.3. Test metamorphic cho Claim Verifier

| # | Tính chất | Bug bắt được |
|---|---|---|
| C1 | **Đổi đội:** "A nhiều X hơn B" là TRUE ⇔ "B nhiều X hơn A" là FALSE (trừ khi bằng nhau) | Nhầm subject / other |
| C2 | **Phủ định:** verdict của "không P" = đảo verdict của "P" (TRUE↔FALSE; PARTLY và INSUFFICIENT giữ nguyên) | Xử lý phủ định |
| C3 | **Bất biến ngôn ngữ:** cùng claim bằng VI và EN → cùng verdict, cùng Fact | Lệch giữa hai bộ từ điển |
| C4 | **Bất biến diễn đạt:** "áp đảo" / "lấn lướt" / "làm chủ thế trận" → cùng verdict | Thiếu từ khoá |
| C5 | **Tất định:** cùng claim, cùng `as_of_seq`, hỏi nhiều lần (kể cả khi replay đang chạy) → JSON giống hệt (bỏ qua thời gian đo) | Rò rỉ trạng thái, view không được ghim |
| C6 | **Khớp oracle:** mọi Fact trong kết quả = `compute()` tính lại độc lập trên view đã ghim | Sai tham số tool call |
| C7 | **Bằng chứng tồn tại:** mọi event ID trong `ClaimResult` có trong log và nằm trong cửa sổ (hoặc là bằng chứng phủ) | Bằng chứng sai |
| C8 | **Không số lạ:** mọi số trong câu giải thích đã công bố đều khớp một Fact | Guardrail có lỗ hổng |

### 13.4. Ma trận bơm lỗi
Ghép mỗi kiểu lỗi của Analyst `{ok, timeout, error, invalid_json ×1, invalid_json ×2, wrong_entity}` với mỗi kiểu lỗi của Explainer `{ok, timeout, error, hallucinate ×1, hallucinate ×2, unsafe}` (36 tổ hợp). Mỗi tổ hợp phải đạt:
- Luôn có `ClaimResult` và **không exception**.
- Verdict **bằng verdict khi chạy toàn mock**, trừ `wrong_entity`. Với `wrong_entity`, spec sai đội phải bị `validate_spec` phát hiện khi tên đội trong claim không khớp, sau đó retry hoặc fallback.
- Câu giải thích đã công bố luôn qua Guardrail.
- Cờ `degraded` và span `fallback` / `retry` trong trace đúng với tổ hợp.
- Tổng thời gian ≤ `CLAIM_BUDGET_S` (dùng timeout nhỏ và đồng hồ giả).

Riêng circuit breaker: 3 lần lỗi thì mở mạch; trong 60 s không gọi LLM; hết 60 s thử lại, thành công thì đóng mạch (thời gian giả lập).

### 13.5. Đồng thời và tất định
- Gửi 20 claim cùng lúc trong khi replay chạy tốc độ cao: tất cả thành công, và Fact của mỗi claim khớp oracle tại `as_of_seq` của chính nó.
- **WebSocket:**
  - Hai client nhận cùng chuỗi `n` và cùng thứ tự event.
  - Client vào muộn nhận `hello` có đủ moment.
  - Client không đọc (queue nhỏ) không làm replay chậm lại, và nhận `resync`.
- Tắt app không để lại task treo.

### 13.6. Bộ eval (P1 nhưng nên làm) — `backend/evals/claims_dominant_loser_42.json`

Dữ liệu là trận golden `dominant_loser-42` (Day 1 v1.3). `as_of` mặc định là hết trận. Mỗi dòng lưu kèm **các con số kỳ vọng**, và một test chạy oracle để kiểm tra lại chúng. Nhờ vậy khi dữ liệu thay đổi, eval báo ngay thay vì âm thầm sai.

| # | Claim | Kỳ vọng | Căn cứ (đã đối chiếu độc lập ở rà soát Day 1) |
|---|---|---|---|
| 1 | Đội nhà áp đảo hiệp 2 | TRUE | Kiểm soát bóng 61%, sút 15–1, xG 2.24–0.24, momentum 0.91 → 4/4 |
| 2 | Harbor dominated the second half | TRUE | Như #1 |
| 3 | Kestrel áp đảo hiệp 2 | FALSE | 0/4 |
| 4 | HAR không áp đảo hiệp 2 | FALSE | Phủ định #1 |
| 5 | HAR có nhiều cú sút hơn KES trong hiệp 2 | TRUE | 15 so với 1 |
| 6 | Harbor had twice as many shots as Kestrel in the second half | TRUE | Tỷ lệ 15 |
| 7 | KES chỉ có 1 cú sút trong hiệp 2 | TRUE | 1 |
| 8 | HAR kiểm soát bóng khoảng 60% trong hiệp 2 | TRUE | 61% (xấp xỉ ±5) |
| 9 | HAR kiểm soát bóng 70% trong hiệp 2 | FALSE | 61% |
| 10 | Kestrel had more corners than Harbor in the second half | FALSE | 0 so với 7 |
| 11 | KES chưa có quả phạt góc nào trong hiệp 2 | TRUE | 0, kèm bằng chứng phủ |
| 12 | Hai đội phạm lỗi ngang nhau trong hiệp 2 | TRUE | 5–5 |
| 13 | HAR pressing mạnh hơn KES trong hiệp 2 | TRUE | PPDA 5.34 so với 13.20 (thấp hơn = mạnh hơn) |
| 14 | HAR áp đảo hiệp 2 nhưng vẫn thua | TRUE | 4/4 và tỷ số chung cuộc 1–2 |
| 15 | HAR áp đảo hiệp 2 và thắng trận | PARTLY_TRUE | Vế 1 đúng, vế 2 sai |
| 16 | HAR có 14 cú sút trong hiệp 2 | PARTLY_TRUE | Thực tế 15 (lệch 1, giá trị ≥ 5) |
| 17 | Calder ghi bàn trong hiệp 2 | TRUE | Bàn 63' |
| 18 | Kestrel won despite lower xG | TRUE | Được bảo đảm bởi expectation của kịch bản |
| 19 | HAR áp đảo hiệp 2 (hỏi tại phút 30) | INSUFFICIENT_DATA | `future_window` |
| 20 | Trọng tài thiên vị Harbor | INSUFFICIENT_DATA | `unsupported_claim` |

- **Bộ held-out:** 10 cách diễn đạt **viết riêng**, không dùng để chỉnh parser, ví dụ "Harbor dồn ép trong 45 phút cuối", "đội khách gần như không sút được hiệp hai". Bộ này dùng để đo khả năng tổng quát hoá một cách trung thực.
- **Chỉ số và ngưỡng:**

  | Chỉ số | Mock | LLM thật |
  |---|---|---|
  | Độ chính xác trên 20 claim | 100% | ≥ 90% |
  | **Tỷ lệ verdict sai** (trên cả 2 bộ) | **0** (không hiểu thì phải ra `INSUFFICIENT_DATA`) | **0** |
  | Độ phủ held-out (không phải INSUFFICIENT) | Báo cáo | ≥ 80% |
  | Số câu nháp của LLM bị Guardrail chặn | Báo cáo (hallucination rate) | Báo cáo |
  | Số bị lọt ra ngoài | Luôn = 0 | Luôn = 0 |
  | Độ trễ p50 / p95 | Báo cáo | Báo cáo |

### 13.7. Test live (chỉ chạy khi có biến môi trường)
- Đánh dấu `@pytest.mark.live`; tự bỏ qua nếu thiếu `GITHUB_TOKEN` / biến Foundry.
- **Nội dung:**
  - 3 claim smoke.
  - Chạy bộ eval: khoảng 20 claim × tối đa 2 lần gọi = 40 request, vừa giới hạn hằng ngày của GitHub Models.
  - Kiểm tra structured output.
  - Kiểm tra map `content_filter`.
- Không chạy trong CI.

### 13.8. Ngân sách hiệu năng
- Claim chạy mock trong process: p95 < 150 ms.
- `POST /claims/verify` qua `TestClient`: p95 < 300 ms.
- MomentDetector: trung bình < 2 ms mỗi event.
- Test perf bỏ qua khi đo coverage (như Ngày 1).
- Khi dùng LLM thật: chỉ đo và báo cáo. Mục tiêu claim < 5 s p95, nằm trong `CLAIM_BUDGET_S = 8`.

### 13.9. CI
- Giữ workflow của Ngày 1. Bước test vẫn là `-m "not slow and not perf and not live"` có coverage, và bước perf chạy riêng.
- Không cần secret.
- Thêm test quy tắc import một chiều (mục 2.3).

### 13.10. Nghiệm thu thủ công (15 phút)
1. `pundit claim "Đội nhà áp đảo hiệp 2" --scenario dominant_loser --seed 42 --trace` → TRUE, có 4 cặp số và trace đủ các bước.
2. `MOCK_LLM_FAULTS=explainer:hallucinate:2 pundit claim ...` → trace có `rejected`, rồi `fallback`; câu công bố là template. **Đây là cảnh demo Guardrail.**
3. `pundit serve`, mở `/docs` (Swagger) và thử `POST /claims/verify`. Dùng một client WebSocket đơn giản xem `hello` / `event` / `moment`.
4. `pundit moments --scenario comeback_var --seed 7` → có `goal_overturned` ở khoảng 37'.
5. Nếu đã có token: `pundit eval --provider github` và ghi kết quả vào mục 18.

---

## 14. Quy trình thực hiện (thứ tự + gate)

| # | Bước | Thời lượng | Đầu ra | Gate |
|---|---|---|---|---|
| 0 | Spike MAF (+ GitHub Models nếu đã có token) | 1:00 | Ghi chú API ở mục 9.5, phiên bản ghim trong `pyproject` | Workflow thử có rẽ nhánh, vòng lặp, chạy song song |
| 1 | Contracts + xuất schema | 0:45 | `contracts.py`, schema mới | Test contract |
| 2 | Ngôn ngữ, thực thể, phạm vi thời gian, từ điển | 1:15 | `language`, `entities`, `timescope`, `lexicon` | Bảng test |
| 3 | RuleClaimAnalyst + `validate_spec` | 1:30 | `analyst_rules`, `spec_validation` | ≥ 60 ca + fuzz |
| 4 | Judge + rubric (hiệu chỉnh trên dữ liệu sinh) | 1:30 | `judge`, `rubric`, bảng hiệu chỉnh trong tài liệu | Bảng gộp verdict vét cạn, C1–C2, C6 |
| 5 | Template + Guardrail | 1:30 | `explain_templates`, `guardrail` | Mutation test; template luôn qua |
| 6 | Trace + resilience + mock bơm lỗi | 1:00 | `trace`, `resilience`, `llm/mock` | Test circuit breaker |
| 7 | MAF workflow + ClaimService + tools | 1:30 | `orchestration/*` | Luồng chuẩn + ma trận lỗi 36 tổ hợp |
| 8 | MomentDetector | 1:00 | `moments` | Lô = luồng; moment theo kịch bản |
| 9 | Session + API + WebSocket + CLI | 2:00 | `api/*`, lệnh CLI mới | Test API/WS, test đồng thời |
| 10 | Bộ eval + `pundit eval` | 1:00 | File eval, runner | Mock 100%; tỷ lệ verdict sai = 0 |
| 11 | Adapter LLM thật (GitHub Models) + test live | 1:00 | `llm/maf_backend`, `prompts` | `-m live` xanh (nếu có token) |
| 12 | Gate cuối | 0:30 | Coverage, tài liệu, checklist, commit | DoD tầng A (+ B nếu có token) |

**Tổng khoảng 14 giờ.** Nếu trễ, cắt theo thứ tự:
1. Moment `momentum_shift` / `dangerous_spell` → chuyển sang Ngày 3.
2. Bộ held-out rút còn 5 claim.
3. Circuit breaker rút gọn (chỉ còn đếm lỗi).
4. Bước 11 (chờ token / Azure).

**Không bao giờ cắt:** bước 1–7, `POST /claims/verify`, các test Guardrail và test ma trận lỗi.

---

## 15. Bảng chống bug

| Loại bug | Ví dụ | Phòng tránh (thiết kế) | Phát hiện (test) |
|---|---|---|---|
| LLM bịa số | "kiểm soát bóng 65%" trong khi thực tế 61% | Guardrail + retry + template | Mutation test, C8, ma trận lỗi |
| LLM hiểu sai đội | Đổi HAR thành KES | `validate_spec` + đối chiếu tên trong claim | C1, `wrong_entity` |
| Số liệu xê dịch giữa các bước | Replay chạy tiếp trong lúc đang kiểm chứng | Ghim `as_of_seq` | C5, test đồng thời |
| Phán bừa khi không hiểu | Claim mơ hồ ra FALSE | Fail-safe → `INSUFFICIENT_DATA` | Held-out: tỷ lệ verdict sai = 0 |
| Vòng retry vô hạn | Guardrail ↔ Explainer | `attempt` trong message, giới hạn cứng | Ma trận lỗi |
| Request treo | LLM không phản hồi | Timeout từng bước + ngân sách tổng + circuit breaker | Test timeout với đồng hồ giả |
| Client WS chậm làm nghẽn | Replay đứng hình | Hàng đợi có giới hạn + `resync` | Test client chậm |
| Mất message / sai thứ tự | Dashboard hiển thị sai tỷ số | Một writer, `n` tăng dần, `hello` / `resync` | Test 2 client |
| Bằng chứng rỗng | "0 phạt góc" không có event | Bằng chứng phủ | C7, test MISSING_EVIDENCE |
| Sai chiều chỉ số | "pressing mạnh hơn" hiểu thành PPDA cao hơn | Polarity trong từ điển | Eval #13 |
| Lỗi chuẩn hoá tiếng Việt | NFD, không dấu, chữ "đ" | `language.py` | Bảng test entities |
| Trích nhầm số | "hai đội", "hiệp 2", "không" bị coi là số | Luật danh từ chỉ số + danh sách ngữ cảnh được phép | Bảng trích số |
| Bàn bị VAR huỷ vẫn được tính | "SAL ghi 3 bàn" | Dùng `select()` có loại bỏ bàn bị huỷ (Day 1) | Test claim trên `comeback_var` |
| Lệch hợp đồng API ↔ frontend | Đổi tên trường | Xuất JSON Schema + contract test | Contract test |
| Lộ token | Token xuất hiện trong log hoặc trace | Che thông tin nhạy cảm; `.env` không commit | Test quét trace tìm chuỗi token giả |
| Task rò rỉ khi tắt app | Replay chạy ngầm mãi | Huỷ task khi shutdown | Test shutdown |
| API MAF thay đổi | Đổi tên lớp | Ghim phiên bản + spike + adapter cô lập | Test workflow |

---

## 16. Rủi ro và phương án

| Rủi ro | Khả năng | Phương án |
|---|---|---|
| API của MAF khác tài liệu, hoặc lỗi trên Python 3.12 | Trung bình | Spike ở bước 0; nếu thất bại dùng runner `asyncio` (mục 9.5) |
| GitHub Models giới hạn request hoặc đổi chính sách (đang preview) | Trung bình | Mock là mặc định; test live tốn rất ít request; Foundry là đích cuối |
| Bộ phân tích rule-based không phủ hết cách nói | Cao | Fail-safe; LLM là đường chính khi có; bộ held-out đo độ phủ một cách trung thực |
| Rubric "áp đảo" gây tranh cãi | Trung bình | Hằng số có tài liệu; hiển thị đủ 4 chỉ báo để người xem tự đánh giá; hiệu chỉnh trên dữ liệu |
| LLM thật chậm (> 5 s) | Trung bình | Một lần gọi Analyst; INSUFFICIENT dùng template; ngân sách + fallback |
| Azure chưa có trước ngày nộp bài | Trung bình | Giữ GitHub Models (thuộc GitHub, cũng là hero tech); Foundry gắn vào chỉ cần đổi biến môi trường |
| Ngày 2 quá tải (14 giờ) | Cao | Thứ tự cắt ở mục 14 |

---

## 17. Hợp đồng cho Ngày 3+

| Hợp đồng | Bên dùng | Quy tắc thay đổi |
|---|---|---|
| `Fact`, `Guardrail.check()` | Talking-Point, Commentator, Graphics (Ngày 3) | Chỉ thêm trường |
| `Moment` + luồng `moment` trên WebSocket | Talking-Point (Ngày 3), dashboard (Ngày 4) | Thêm `kind` thoải mái; không đổi nghĩa kind đã có |
| `ClaimResult`, `ClaimRequest`, message WS `v1` | Dashboard (Ngày 4) | Nâng `v` khi đổi hoặc xoá trường |
| Catalog tool (`orchestration/tools.py`) | Agent Ngày 3, MCP server (Ngày 6) | Không đổi tên tool |
| `ExplainerPort` + `TemplateExplainer` | Mẫu cho các bộ sinh câu chữ khác | — |
| `TraceSpan` | Drawer "Agent Trace" (Ngày 4), App Insights (Ngày 5) | Chỉ thêm trường |

---

## 18. Quyết định đã chốt (20/10/2026)

| # | Quyết định | Kết quả |
|---|---|---|
| 1 | Ngôn ngữ claim | ✅ **Tiếng Việt + tiếng Anh**; câu giải thích theo ngôn ngữ của claim |
| 2 | LLM thật tạm thời khi chưa có Azure | ✅ **GitHub Models** (endpoint tương thích OpenAI, dùng chung adapter với Foundry); mock vẫn là mặc định cho dev / CI |
| 3 | Orchestrator | ✅ **Microsoft Agent Framework Workflows**, phiên bản ghim sau spike; dự phòng là runner `asyncio` |
| 4 | Vai trò của LLM | ✅ Chỉ hiểu claim (structured output) và viết câu giải thích; phán quyết và số liệu do code tất định |
| 5 | Kết quả spike | ⏳ Ghi vào mục 9.5 sau bước 0 |
| 6 | Model GitHub Models / Foundry | ⏳ Chọn sau spike / khi có Azure |
