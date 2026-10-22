# Detailed Design — Ngày 5 (25/10): Giọng đọc tiếng Việt · SSML · Recap audio · Chọn giọng · Speech-to-Text

> Liên quan: [claude.md](../claude.md) · [checklist.md](../checklist.md) (Ngày 5, phần "Giọng đọc tiếng Việt") · [day3-design.md](day3-design.md) (§3.5 bình luận, §8.3 `excitement` / `speak`, §10 recap, §11 cá nhân hoá, §21 hợp đồng) · [day4-design.md](day4-design.md) (§7.5 chỗ chừa cho giọng đọc, §16 hợp đồng cho Ngày 5)
> Phiên bản: v1.0 (22/10/2026) · Người thực hiện: 1 người · Backend Python 3.12 (chỉ thêm) · Frontend Next.js 16 (chỉ thêm)
> Phạm vi: chỉ phần **Giọng đọc tiếng Việt** của Ngày 5. Phần triển khai Azure (Dockerfile, Bicep, Container Apps, App Insights) có tài liệu riêng; §17 ghi các hợp đồng mà phần đó phải theo.
> Chiến lược: **mock-first**, vì chưa có Azure (đã chốt 22/10). Dev và CI chạy `MockSynthesizer` (WAV tất định). Trước khi có Azure, muốn nghe tiếng Việt thật thì dùng **giọng của trình duyệt**, đọc đúng kịch bản mà server đã chuẩn hoá. Azure AI Speech gắn sau, chỉ bằng biến môi trường. Các thông số Azure trong tài liệu đã đối chiếu với Microsoft Learn ngày 22/10/2026 (§6.1, §8.3); chỗ nào chưa xác minh được thì ghi rõ "xác minh ở G0" hoặc "xác minh ở tầng B".

---

## 0. Mục tiêu, phạm vi và Definition of Done

Ngày 3 đã sinh bình luận VI / EN theo hồ sơ người xem; mỗi mục có `excitement` (0–3) và `speak` (gợi ý đọc thành tiếng). Ngày 4 có Fan VN Player hiển thị chữ và chừa sẵn giao diện `AudioQueue`. Ngày 5 thêm **giọng đọc** cho các nội dung đó.

| Đầu ra | Dành cho | Ưu tiên |
|---|---|---|
| **Giọng đọc bình luận real-time** trên Fan VN Player: mỗi mục `speak` thành một clip audio, phát nối tiếp, bàn thắng được chen ngang | Fan Việt | P0 |
| **SSML nhấn giọng / tăng tốc độ** theo loại tình huống và `excitement` (bàn thắng, cơ hội lớn, đợt ép sân, VAR…) | Fan, video demo | P0 |
| **Recap audio** H1 / FULL: tóm tắt + các khoảnh khắc then chốt, lưu vào kho bền (thư mục local, lên Azure là Blob Storage); nút nghe, tua, nghe lại | Fan | P0 |
| **Giọng dự phòng của trình duyệt** khi Azure lỗi, hết quota hoặc chưa có (đã chốt) | Fan, dev trước khi có Azure | P0 |
| **Chọn giọng nữ / nam** (`vi-VN-HoaiMyNeural` / `vi-VN-NamMinhNeural`) | Fan | P1 |
| **Lời chào khi bật giọng**: nói rõ đây là giọng AI đọc dữ liệu tổng hợp, đồng thời thử loa | Fan, Responsible AI | P1 |
| **Speech-to-Text**: bình luận viên nói claim trong Studio (đã chốt: thiết kế đủ, làm cuối, cắt đầu tiên) | Studio | P2 |
| Giọng tiếng Anh (đã chốt: VI trước, EN bật bằng cấu hình) | Fan EN | P2 |

**Definition of Done** (ba tầng như các ngày trước):

| Tầng | Điều kiện | Kiểm tra bằng |
|---|---|---|
| **A — mock + giọng trình duyệt, local** (bắt buộc trong Ngày 5) | 1. `pundit serve --ui`, Fan hồ sơ casual · vi · HAR · Người mới xem, bật giọng: với `TTS_PROVIDER=mock`, client phát clip đúng thứ tự, không bao giờ hai tiếng cùng lúc, bàn thắng chen ngang được câu bình thường; với `TTS_PROVIDER=browser` trên Edge nghe được tiếng Việt đọc đúng số. 2. Kịch bản đọc của mọi mục `speak` và mọi recap (4 kịch bản × 3 seed) qua SpeechGuard: **0 chữ số sót lại** trong câu đọc tiếng Việt, **mọi con số khứ hồi đúng**, SSML luôn hợp lệ. 3. Recap H1 / FULL (vi × analyst / casual × nữ / nam) có audio, nằm trong kho bền, nghe được, tua được (206), nghe lại được; adapter Blob chạy với Azurite. 4. Đổi giọng nữ ↔ nam đổi đúng clip. 5. Bơm lỗi TTS (mock): không crash, client chuyển sang giọng trình duyệt, breaker mở rồi đóng, `/healthz` báo đúng. 6. Hồ sơ không có `voice` nhận payload như Ngày 4, chỉ thêm các khoá mới mang giá trị `null` (`speech`, `profile.voice`); `TTS_PROVIDER=off`: toàn bộ test Ngày 1–4 xanh. 7. Replay 1000x với 3 hồ sơ có giọng chậm thêm không quá 20 %; event → clip sẵn sàng (mock trễ 300 ms) p95 < 1,5 s. 8. (P2, nếu làm) Mock STT điền vào ô claim, không tự gửi | `pytest -m "not live"`, `npm run test`, `npx playwright test`, `scripts/smoke_voice.py` (§13) |
| **B — Azure Speech thật, local** (khi có key) | Giọng HoaiMy / NamMinh qua REST; nghe duyệt toàn bộ bộ câu (V10); hiệu chỉnh tốc độ đọc ước tính; event → âm thanh p95 < 5 s ở 1x và 20x; không gặp 429 trong một trận 20x với 2 hồ sơ có giọng trên F0 | `pytest -m live`, `scripts/tts_review.py --provider azure`, `scripts/smoke_voice.py --provider azure` |
| **C — trên Azure** (phần triển khai Ngày 5) | Managed Identity gọi Speech (`aad#…`), recap lưu Blob thật, log `speech.synthesize` vào App Insights | Smoke test trên URL public (§17) |

**Ngoài phạm vi Ngày 5 (phần giọng đọc):**
- Dockerfile, Bicep, deploy, App Insights: tài liệu triển khai riêng, theo hợp đồng ở §17.
- Truyền từng byte audio qua WebSocket hoặc stream một clip đang tổng hợp dở. Clip bình luận chỉ 3–9 s và dự kiến tổng hợp xong trong khoảng một giây (đo ở tầng B); recap được tạo sẵn ngay khi có (§18 dòng 1).
- Giọng ở Studio, overlay và màn so sánh. Nút đọc lại một câu cũ trên thẻ bình luận.
- Speech SDK (thư viện native), custom voice, viseme / avatar, `mstts:express-as`.
- Phụ đề tiếng Anh cho giọng Việt: làm khi dựng video ở Ngày 7.

### 0.1. Đối chiếu checklist Ngày 5 → thiết kế → test

| Mục checklist | Ưu tiên | Thiết kế | Test |
|---|---|---|---|
| Tích hợp Azure AI Speech TTS cho bình luận real-time (stream audio về Fan VN Player) | P0 | §4.1, §5, §8, §9, §10 | §12.2; V3–V7; E2E A1–A5, A7–A9; smoke V9; tầng B (§13) |
| SSML: nhấn giọng / tăng tốc độ khi có bàn thắng hoặc tình huống nguy hiểm | P0 | §6 | test vàng preset và SSML; property SSML; V10 nghe duyệt |
| Recap audio (1–2 phút) lưu vào Blob Storage, có nút nghe lại | P0 | §4.2, §8.8, §9.3, §10.6 | độ dài ước tính §4.2; kho bền local + Azurite; E2E A6 |
| Chọn giọng nam / nữ | P1 | §3.1, §10.5 | parse hồ sơ; E2E A4 |
| Speech-to-Text: bình luận viên nói claim | P2 | §11 | API mock; E2E A10; tầng B: live |

---

## 1. Nguyên tắc thiết kế (bổ sung S1–S9 của Ngày 3 và U1–U10 của Ngày 4)

| # | Nguyên tắc | Áp dụng |
|---|---|---|
| V1 | **Không có bằng chứng thì không đọc** | Chỉ đọc văn bản đã qua Guardrail (bình luận, đoạn recap) hoặc văn bản tĩnh đã duyệt bằng test (câu mở đầu theo CLB, bản ngắn của lời giải thích luật, câu dẫn của bản đọc). Bộ dựng kịch bản đọc nhận model có kiểu, không nhận chuỗi tự do |
| V2 | **Đọc đúng từng con số** | Mọi chữ số được đổi thành chữ bằng code tất định (§5), không giao cho TTS tự đoán: locale vi-VN coi "." là dấu nghìn, nên "2.24" có thể bị đọc thành "hai nghìn hai trăm bốn mươi". SpeechGuard kiểm tra khứ hồi từng số (§7) |
| V3 | **Chữ trước, tiếng sau** | Message `commentary` không bao giờ chờ audio. Audio là lớp thêm vào; hỏng thì người xem vẫn có chữ |
| V4 | **Một kịch bản đọc, hai cách phát** | Server dựng `SpeechCue`: các đoạn văn bản, mỗi đoạn có prosody. Azure đọc SSML dựng từ cue này; trình duyệt đọc đúng các đoạn đó với cùng rate / pitch / volume. Client không tự viết câu, không tự tính tốc độ (U1) |
| V5 | **Chỉ tổng hợp khi có người nghe** | Chỉ kết nối có `voice` trong hồ sơ, chỉ message live. `hello`, `resync` và REST bình luận không bao giờ kích hoạt tổng hợp |
| V6 | **Định danh theo nội dung** | `clip_id = sha256(engine, giọng, định dạng, SSML)`: cùng nội dung thì cùng file, mọi người nghe dùng chung, khởi động lại không tốn quota, file bất biến nên cache được vĩnh viễn |
| V7 | **Replay và chữ không bao giờ chờ TTS** | `Narrator.offer()` không chờ. Tổng hợp chạy nền qua một cổng giới hạn (số request mỗi phút, số request đồng thời, ưu tiên), có breaker riêng và ngân sách ký tự mỗi ngày |
| V8 | **Mỗi lúc chỉ một tiếng** | Bộ lập lịch phía client là hàm thuần; property test bảo đảm không bao giờ phát hai clip cùng lúc và hàng đợi luôn có giới hạn |
| V9 | **Tất định để test** | WAV của mock tất định; đồng hồ tiêm vào; không dùng `random`, `hash()`, `uuid4` (luật ruff đã cấm) |
| V10 | **Chỉ thêm** | Hồ sơ không có `voice` nhận payload như Ngày 4, chỉ thêm các khoá mới mang giá trị `null` (`speech`, `profile.voice`); `Profile.key()` không đổi khi không có `voice`; `TTS_PROVIDER=off` trả lại đúng app Ngày 4; schema chỉ thêm trường |
| V11 | **Không lộ bí mật** | Key và token là `SecretStr`, không vào log, trace, `/healthz`; audio của STT không được lưu, không được ghi log |
| V12 | **Minh bạch giọng AI** | Lời chào và phần mở đầu recap nói rõ đây là giọng do AI tạo, đọc dữ liệu tổng hợp. UI ghi nguồn giọng (Azure hay trình duyệt). Theo hướng dẫn công bố giọng tổng hợp của Microsoft |

---

## 2. Kiến trúc

### 2.1. Luồng tổng thể

```
StudioDirector ─► MatchSession.publish_studio("commentary", item)          (Ngày 3, không đổi)
  _publish_commentary: render một lần cho mỗi khoá hồ sơ
    └─ hồ sơ có voice, item.speak, message live ─► Narrator (agent mới)
         narration.commentary_script ─► spoken.normalize ─► prosody ─► ssml ─► SpeechGuard
         ├─ CommentaryView.speech = SpeechCue + AudioRef(url)      (trả về ngay, không chờ)
         └─ task nền, single-flight theo clip_id:
              ClipCache? ─► DurableStore? ─► RateGate ─► Synthesizer (mock | Azure REST)
                                                         ─► ClipCache (+ DurableStore nếu là Azure)
  Hub.publish_each("commentary") ─► WS ─► Fan: useCommentaryAudio
       scheduler (hàm thuần) ─┬─► AudioPlayer: một HTMLAudioElement, GET /audio/{clip_id}.mp3
                              └─► BrowserVoice: speechSynthesis, khi không có audio của server

Recap H1 / FULL (rev 1 template, rev 2 LLM) ─► Narrator (recap_script) ─► ClipCache + DurableStore (local → Blob)
Fan bấm "Nghe tóm tắt" ─► GET /matches/{id}/recap?…&voice= ─► RecapView.speech ─► <audio controls>

Studio ClaimBox 🎤 ─► POST /speech/recognize (WAV 16 kHz) ─► Transcriber (mock | Azure STT) ─► điền ô claim   (P2)
```

- **"Stream audio" được hiểu là luồng audio theo câu.** Mỗi câu bình luận mang tham chiếu clip ngay trong message WS của nó; nhờ tổng hợp trước, clip thường sẵn sàng khoảng một giây sau (đo ở tầng B). Client phát các clip thành một dòng liên tục. Clip được phục vụ qua HTTP, định danh theo nội dung, cache vĩnh viễn. Không gửi byte audio qua WebSocket: socket của Ngày 4 chỉ nhận JSON (khung nhị phân bị đếm là `bad_frames`).
- **Tổng hợp trước** (prefetch) ngay khi message được phát, nên lúc client xin clip thì clip thường đã xong.

### 2.2. Agent mới

| Agent | Loại | Vào → Ra | LLM? |
|---|---|---|---|
| **Narrator** (Giọng đọc) | Tất định + gọi tool (Azure Speech) | `CommentaryItem` + `Profile` (đã cá nhân hoá) → `SpeechScript` → `SpeechCue` → clip audio | Không |
| **Transcriber** (P2) | Gọi tool (Azure STT) | WAV 16 kHz → `RecognitionResult` → chữ trong ô claim → `ClaimRequest` (người dùng tự gửi) | Không |

- **Narrator chạy sau Personalization, ngoài workflow MAF.** Workflow studio sinh item chuẩn (canonical) một lần; cá nhân hoá diễn ra lúc phát, theo từng khoá hồ sơ, và kịch bản đọc phụ thuộc hồ sơ (ngôn ngữ, giọng, câu mở đầu theo CLB, lời giải thích luật). Đưa Narrator vào workflow sẽ bắt mỗi job tổng hợp cho mọi hồ sơ có thể có, kể cả khi không ai nghe.
- **Đóng góp cho tiêu chí Best Multi-Agent System:**
  - vai trò riêng (biến nội dung đã kiểm chứng thành giọng nói an toàn);
  - handoff có kiểu `CommentaryView → SpeechScript → SpeechCue → AudioRef`;
  - shared state: kho clip theo nội dung dùng chung cho mọi phiên và mọi người nghe; tốc độ và trạng thái của phiên;
  - phục hồi khi lỗi: breaker → giọng trình duyệt; SpeechGuard chặn → im lặng, không đọc sai;
  - trace: span của Narrator hiện trong drawer Agent Trace (§8.9).

### 2.3. File mới và file đổi

**Backend:**

```
backend/src/pundit/
├─ agents/                        # thuần, không I/O
│  ├─ voices.py                   # bảng giọng theo (ngôn ngữ, giới tính); ngôn ngữ đang bật
│  ├─ spoken_numbers.py           # số → chữ VI (và EN, P2); bộ đọc ngược chữ → số (oracle cho test và guard)
│  ├─ spoken.py                   # chuẩn hoá cách đọc: số, từ điển đọc theo trận, viết tắt, ký hiệu (§5)
│  ├─ prosody.py                  # preset prosody, chọn preset theo kind / excitement (§6)
│  ├─ narration.py                # kịch bản đọc: bình luận, recap, lời chào; ngân hàng câu tĩnh (§4)
│  ├─ ssml.py                     # dựng SSML, escape, kiểm tra hợp lệ (§6.5)
│  └─ speech_guard.py             # SpeechGuard (§7)
├─ speech/                        # I/O: tầng mới giữa orchestration và api
│  ├─ ports.py                    # Synthesizer, Recognizer, request / result, lỗi có kiểu
│  ├─ mock.py                     # MockSynthesizer (WAV tất định, bơm lỗi), MockRecognizer
│  ├─ azure_tts.py                # AzureSynthesizer: REST qua httpx (§8.3)
│  ├─ azure_stt.py                # AzureRecognizer: REST short audio (P2, §11)
│  ├─ auth.py                     # key hoặc Entra ID (aad#resourceId#token), cache token
│  ├─ gate.py                     # RateGate: token bucket + đồng thời + ưu tiên + Retry-After
│  ├─ audio.py                    # ghi WAV, nhận diện MP3, tính thời lượng
│  ├─ clips.py                    # ClipCache: thư mục local, LRU theo dung lượng, ghi nguyên tử
│  ├─ durable.py                  # DurableStore: LocalStore | BlobStore (azure-storage-blob)
│  └─ narrator.py                 # Narrator: offer / produce / wait, single-flight, breaker, ngân sách, số đo
└─ api/
   └─ audio.py                    # GET|HEAD /audio/{clip}, /speech/greeting, /speech/voices, POST /speech/recognize
```

| File cũ | Thay đổi (chỉ thêm) |
|---|---|
| `agents/contracts.py` | `VoiceGender`, `ProsodyPreset`, `SpeechNote`, `SpeechSegment`, `SpeechCue`, `AudioRef`, `RecognitionResult`; `Profile.voice`; `CommentaryView.speech`, `RecapView.speech` |
| `agents/personalize.py` | `parse_profile` đọc `voice`; `render_commentary` / `render_recap` nhận callback `speech` (giống `tracker`, `metric_facts`) |
| `orchestration/resilience.py` | `CircuitBreaker.trip(cooldown_s)`: mở ngay, dùng cho lỗi xác thực |
| `llm/settings.py` | Biến `TTS_*`, `SPEECH_*`, `AUDIO_*`, `MOCK_TTS_*`, `STT_*` (§3.7); kiểm tra cấu hình lúc khởi động; `describe()` thêm khối `speech` (không có bí mật) |
| `api/sessions.py` | Render bình luận live và recap với `speech`; `hello` không có `speech`; số đo |
| `api/routes.py` | `/healthz` thêm khối `speech`; `/recap` nhận `voice`; `/studio/{item_id}` gộp span của Narrator |
| `api/app.py` | Dựng `Narrator` dùng chung cho app; mount router `audio`; đóng Narrator khi tắt app |
| `cli.py` | `pundit speak`, `pundit tts warm`, `pundit recap --audio` (§9.5) |
| `tests/contract/test_import_rules.py` | Tầng `speech`; quy tắc thư viện ngoài (§2.5) |
| `scripts/record_ws.py` | Thêm hồ sơ có `voice` khi ghi fixture |
| Mới: `scripts/smoke_voice.py`, `scripts/tts_review.py` | Smoke test giọng đọc (§13); trang nghe duyệt (V10) |

**Frontend:**

```
frontend/src/
├─ lib/audio/
│  ├─ scheduler.ts                # bộ lập lịch hàng đợi đọc (hàm thuần, §10.3)
│  ├─ player.ts                   # AudioPlayer: một HTMLAudioElement, mở khoá, sự kiện ended / error
│  ├─ browser-voice.ts            # BrowserVoice: speechSynthesis, chọn giọng, tách câu
│  ├─ unlock.ts                   # mở khoá autoplay trong cử chỉ người dùng
│  └─ recorder.ts                 # ghi WAV 16 kHz (P2, tải động)
├─ hooks/useCommentaryAudio.ts    # nối message live → scheduler → player
├─ components/fan/VoiceControl.tsx · RecapAudio.tsx · AudioQueue.ts (cài phần phát, giữ export cũ)
└─ components/studio/MicButton.tsx   # P2
frontend/public/audio/pcm-worklet.js  # AudioWorklet ghi PCM (P2)
```

| File cũ | Thay đổi |
|---|---|
| `hooks/useMatchConnection.ts` | Tuỳ chọn `onLive(message)`: gọi đồng bộ cho mỗi message hợp lệ, trước khi gom khung hình |
| `lib/profile.ts` | `voice: "female" \| "male" \| null` ↔ query `voice`; `coerceProfile` đọc trường mới |
| `app/fan/page.tsx` · `FanFeed.tsx` · `CommentaryCard.tsx` · `ProfileSetup.tsx` | `VoiceControl`; thẻ đang được đọc có `data-speaking`; chọn giọng trong phần nâng cao; `RecapAudio` trong tab Tóm tắt |
| `components/studio/ClaimBox.tsx` | Nút micro (P2) |
| `lib/api.ts` · `lib/i18n/{vi,en}.ts` | Recap có `voice`, `/speech/greeting`, `/speech/voices`, `/speech/recognize`; khoá chữ mới |

### 2.4. Dependency

| Gói | Lý do | Ghi chú |
|---|---|---|
| `httpx` | Gọi REST của Azure Speech | Bản 0.28.1 đã có sẵn (cài kèm `openai`); khai báo tường minh vì `speech` dùng trực tiếp |
| `azure-identity` | Managed Identity / `az login` cho Speech (Entra) và Blob | Import trễ trong `speech/auth.py`, `speech/durable.py`; dev và CI không cần đăng nhập Azure. Dùng client **đồng bộ** trong `asyncio.to_thread` (token chỉ lấy khoảng một lần mỗi giờ), nên không cần `aiohttp` |
| `azure-storage-blob` | `BlobStore` cho recap và clip Azure | Như trên. Chạy được với Azurite qua connection string |
| Azurite (dev, Node) | Giả lập Blob để test adapter khi chưa có Azure | `npx azurite-blob --silent --location <tmp>`; không cần Docker. Test có marker `azurite` (opt-in) |

- **Không dùng Speech SDK** (`azure-cognitiveservices-speech`): thư viện native, image Linux cần thêm gói hệ thống, khó test offline; trên máy dev, Windows Application Control đang chặn binary của venv. REST đủ cho đọc theo câu và test được bằng `httpx.MockTransport`.
- Ghim phiên bản chính xác lúc cài, cập nhật `uv.lock`. Frontend không thêm gói: HTMLAudioElement, Web Speech API và AudioWorklet đều là API của trình duyệt.

### 2.5. Tầng import

- Thứ tự mới: `domain ← stats ← state ← engine ← agents ← llm ← orchestration ← speech ← api ← cli`. `speech` dùng được `CircuitBreaker` của `orchestration.resilience` và `Settings` của `llm.settings`.
- Các module mới trong `agents/` không I/O, không import `speech`.
- Thư viện ngoài:
  - `httpx` → `llm.maf_backend`, `speech.azure_tts`, `speech.azure_stt`, `speech.auth`;
  - `azure` → `llm.maf_backend`, `speech.auth`, `speech.durable`.
- `test_import_rules.py` được cập nhật và vẫn chạy trên CI.

---

## 3. Hợp đồng dữ liệu (chỉ thêm)

Mọi model là Pydantic `frozen=True, extra="forbid"` như các ngày trước.

### 3.1. Hồ sơ: `Profile.voice`

```python
VoiceGender = Literal["female", "male"]

class Profile(Contract):
    ...                                   # các trường của Ngày 3
    voice: VoiceGender | None = None      # None: không đọc (mặc định)

    def key(self) -> str:                 # khoá cũ không đổi khi voice là None (V10)
        base = "|".join(...)              # như Ngày 3
        return f"{base}|{self.voice}" if self.voice else base
```

- Query WebSocket / REST: `voice=female|male|off` (không phân biệt hoa thường; `off` hoặc vắng mặt nghĩa là `None`). Giá trị khác: WebSocket đóng `4422` với lý do *"voice must be female, male or off"*, REST trả `422`.
- Giới hạn 32 khoá hồ sơ mỗi phiên của Ngày 3 giữ nguyên.
- Bật giọng hay đổi giọng là mở kết nối mới với query mới, giống công tắc Người mới xem của Ngày 4.

### 3.2. Kịch bản đọc và audio

```python
ProsodyPreset = Literal["calm", "lively", "excited", "peak", "burst", "tension",
                        "explainer", "recap", "greeting"]
SpeechNote = Literal["server_off", "speed", "circuit_open", "throttled", "budget", "guard"]

class SpeechSegment(Contract):
    text: str                             # chữ sẽ đọc: số đã viết thành chữ, tên đã đổi sang cách đọc
    preset: ProsodyPreset
    rate: float = Field(ge=0.5, le=2.0)   # 1.0 = bình thường; dùng chung cho SSML và trình duyệt
    pitch: float = Field(ge=0.5, le=1.5)
    volume: float = Field(ge=0.0, le=1.0)
    pause_before_ms: int = Field(default=0, ge=0, le=1000)

class AudioRef(Contract):
    clip_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    url: str                              # "/audio/{clip_id}.mp3" (cùng origin, tương đối)
    media_type: Literal["audio/mpeg", "audio/wav"]
    duration_ms: int = Field(ge=0)
    exact: bool                           # False: ước tính (clip chưa có); True: đo từ file

class SpeechCue(Contract):
    lang: Literal["vi-VN", "en-GB"]
    voice: str                            # tên giọng Azure, ví dụ "vi-VN-HoaiMyNeural"
    gender: VoiceGender
    preset: ProsodyPreset                 # preset của phần chính
    segments: tuple[SpeechSegment, ...] = Field(min_length=1, max_length=16)
    audio: AudioRef | None = None         # None: server không có audio cho cue này
    note: SpeechNote | None = None        # lý do khi audio là None
```

| `note` | Nghĩa | Client làm gì |
|---|---|---|
| (không có, `audio` có giá trị) | Server có hoặc sắp có clip | Phát clip; clip lỗi thì đọc bằng giọng trình duyệt |
| `server_off` | `TTS_PROVIDER=browser`: server chỉ gửi kịch bản | Đọc bằng giọng trình duyệt |
| `circuit_open` / `throttled` / `budget` | Azure đang hỏng, quá hạn mức hoặc hết ngân sách ngày | Đọc bằng giọng trình duyệt |
| `speed` | Replay nhanh hơn `TTS_MAX_SPEED` | Không đọc |
| `guard` | Kịch bản trượt SpeechGuard (§7) | **Không đọc**, kể cả bằng trình duyệt (V1) |

### 3.3. `CommentaryView.speech` và `RecapView.speech`

```python
class CommentaryView(Contract):
    ...                                   # các trường của Ngày 3
    speech: SpeechCue | None = None

class RecapView(Contract):
    ...
    speech: SpeechCue | None = None
```

- `CommentaryView.speech` chỉ có khi đủ ba điều kiện: hồ sơ có `voice`, `view.speak` là `true` (đã gồm luật `relevance != low` của Ngày 3), và đây là message `commentary` **live**. `hello`, `resync` và `GET /commentary` luôn có `speech: null` (V5).
- `RecapView.speech` có trong message `recap` live (hồ sơ có `voice`) và trong `GET /recap?…&voice=`. Recap trong `hello` có `speech: null`; nút "Nghe tóm tắt" gọi REST (§10.6).
- Ngôn ngữ chưa bật trong `TTS_LANGUAGES` (EN ở cấu hình mặc định): `speech: null`.

### 3.4. WebSocket

- Không thêm loại message; `v` vẫn là `1`. Thứ tự S8 (event → moment → item) không đổi.
- Client Ngày 4 bỏ qua trường lạ, nên không bị ảnh hưởng. Fixture Ngày 4 vẫn hợp lệ với schema mới vì `speech` là trường tuỳ chọn.

### 3.5. REST mới

| Method | Đường dẫn | Mô tả |
|---|---|---|
| `GET`, `HEAD` | `/audio/{clip_id}.{mp3\|wav}` | File clip (§9.2): `200` / `206`; `404` không biết clip; `422` sai định dạng ID; `503` clip lỗi hoặc chờ quá `TTS_GET_WAIT_S` |
| `GET` | `/matches/{id}/recap?scope=&lang=&mode=&voice=` | Như Ngày 3, thêm `speech` khi có `voice` (tổng hợp nếu chưa có) |
| `GET` | `/speech/greeting?lang=vi&voice=female` | `SpeechCue` của lời chào (§4.3) |
| `GET` | `/speech/voices` | Danh sách `{lang, gender, voice, display_name, server_audio}` theo `TTS_LANGUAGES` và provider, cho bộ chọn giọng |
| `POST` | `/speech/recognize?lang=vi\|en` | STT (P2, §11): body `audio/wav` → `RecognitionResult` |

### 3.6. Schema

- `pundit schema` xuất lại `commentary_view`, `recap_view`, `profile`, `ws_payloads`, `health`; thêm `speech_cue`, `audio_ref`, `recognition_result`, `speech_voice`.
- Frontend chạy `npm run gen:types`; CI chặn khi type lệch schema (Ngày 4).
- Contract test: mọi schema cũ chỉ được thêm khoá; fixture Ngày 4 vẫn hợp lệ.

### 3.7. Biến môi trường mới

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TTS_PROVIDER` | `mock` | `off` (app y như Ngày 4) · `browser` (gửi kịch bản đọc, trình duyệt tự đọc; dùng để nghe tiếng Việt trước khi có Azure) · `mock` (WAV tất định; CI, E2E) · `azure` |
| `TTS_LANGUAGES` | `vi` | Ngôn ngữ có giọng; `vi,en` để bật EN (P2) |
| `SPEECH_REGION` | — | Ví dụ `southeastasia`; cần khi dùng key |
| `SPEECH_KEY` | — | Chỉ dev (`SecretStr`). Trên Azure dùng Managed Identity |
| `SPEECH_ENDPOINT` | — | Custom domain `https://<tên>.cognitiveservices.azure.com`; bắt buộc khi dùng Entra |
| `SPEECH_RESOURCE_ID` | — | `/subscriptions/…/providers/Microsoft.CognitiveServices/accounts/<tên>`; dùng cho Entra |
| `TTS_MAX_RPM` | `18` | Request tổng hợp mỗi phút. F0 cho phép 20 / 60 s (không chỉnh được); S0 mặc định 30 / giây |
| `TTS_MAX_CONCURRENCY` | `4` | Request đồng thời tới Azure |
| `TTS_TIMEOUT_S` · `TTS_RECAP_TIMEOUT_S` | `4` · `45` | Timeout một lần tổng hợp bình luận / recap |
| `TTS_GET_WAIT_S` | `10` | `GET /audio` chờ tối đa bấy nhiêu giây cho một clip đang tổng hợp |
| `TTS_MAX_SPEED` | `20` | Replay nhanh hơn mức này thì không đọc (`note=speed`) |
| `TTS_DAILY_CHAR_BUDGET` | `200000` | Ký tự tính phí tối đa mỗi ngày (UTC) mà app được dùng |
| `AUDIO_CACHE_DIR` · `AUDIO_CACHE_MAX_MB` | `<temp>/pundit-audio` · `256` | Cache clip cục bộ (LRU) |
| `AUDIO_STORE` | `local` | Kho bền: `none` · `local` · `blob` |
| `AUDIO_STORE_DIR` | `backend/.data/audio` | Kho bền local (thêm vào `.gitignore`) |
| `AUDIO_BLOB_ACCOUNT_URL` · `AUDIO_BLOB_CONTAINER` | — · `audio` | Blob với Managed Identity |
| `AUDIO_BLOB_CONNECTION_STRING` | — | Chỉ cho Azurite / dev (`SecretStr`) |
| `MOCK_TTS_LATENCY_MS` · `MOCK_TTS_FAULTS` · `MOCK_TTS_TIME_SCALE` | `0` · rỗng · `1.0` | Mock: độ trễ; bơm lỗi (`error`, `timeout:2`, `throttle:1`, `bad_audio:1`, `auth:1`); hệ số độ dài WAV (E2E dùng `0.1`) |
| `STT_PROVIDER` · `STT_MAX_SECONDS` · `MOCK_STT_TEXT` | `off` · `15` · câu claim mẫu | P2 (§11) |

Kiểm tra lúc khởi động (`ConfigError` liệt kê mọi lỗi, như Ngày 2):
- `TTS_PROVIDER=azure` cần (`SPEECH_KEY` + `SPEECH_REGION`) hoặc (`SPEECH_ENDPOINT` + `SPEECH_RESOURCE_ID`).
- `AUDIO_STORE=blob` cần `AUDIO_BLOB_ACCOUNT_URL` hoặc `AUDIO_BLOB_CONNECTION_STRING`.
- `TTS_LANGUAGES` chỉ nhận ngôn ngữ có trong bảng giọng.
- `.env.example` chỉ ghi tên biến và chú thích, không có giá trị bí mật.

---

## 4. Kịch bản đọc (`agents/narration.py`)

Kịch bản đọc (`SpeechScript`) là danh sách đoạn `(nguồn, văn bản viết, preset, khoảng nghỉ trước)`. Nguồn luôn là một trong các loại có kiểu ở bảng dưới (V1). Kịch bản đi qua chuẩn hoá (§5), prosody (§6) và SpeechGuard (§7) rồi mới thành `SpeechCue`.

| Nguồn | Đã kiểm chứng bằng |
|---|---|
| `CommentaryText` của mục (đúng bản ngôn ngữ / giọng của hồ sơ) | Guardrail studio của Ngày 3 |
| `framing` (câu mở đầu theo CLB) | Test nội dung tĩnh của Ngày 3 |
| Bản **ngắn** của lời giải thích luật (`rules_library`) | Test nội dung tĩnh của Ngày 3 |
| Đoạn recap (`RecapText.paragraphs`) | Guardrail studio (recap) |
| Câu dẫn của bản đọc (lời chào, mở đầu, kết recap, "Phút X:") | Ngân hàng câu tĩnh (§4.4) + test |

### 4.1. Bình luận

| Thứ tự | Đoạn | Có khi | Preset | Nghỉ trước |
|---|---|---|---|---|
| 1 | `framing` | Hồ sơ casual có CLB và mục có câu mở đầu | Như phần chính | 0 |
| 2 | Câu cảm thán đầu (burst) | Preset chính là `peak` hoặc `excited`, và câu đầu kết thúc bằng "!" và có tối đa 4 từ ("VÀOOO!", "Suýt nữa!", "Bàn thắng!") | `burst` | 150 ms sau câu mở đầu; 0 nếu là đoạn đầu |
| 3 | Phần chính: `text` (phần còn lại nếu đã tách burst) | Luôn có | Theo §6.3 | 250 ms sau burst; 150 ms sau câu mở đầu; 0 nếu là đoạn đầu |
| 4 | Giải thích luật: "Luật {tiêu đề}: {bản ngắn}" | Hồ sơ bật Người mới xem và `view.explanation` khác `null` | `explainer` | 600 ms |

- Đoạn 4 luôn dùng **bản ngắn** (≤ 140 ký tự, khoảng 9 s đọc), kể cả khi thẻ hiện bản đầy đủ: bản đầy đủ (≤ 420 ký tự) đọc mất gần 30 s, làm lỡ các câu tiếp theo. Luật được giải thích là luật mà `explanation` của chính view đó chọn (`seen_rules` của Ngày 3 không đổi).
- Độ dài: mục `speak` tiếng Việt trung bình 77–86 ký tự (analyst) và 54–56 ký tự (casual), dài nhất 138 ký tự (đo trên `dominant_loser-42`, `comeback_var-7`, `red_card_swing-7`), tức clip khoảng 3–9 s. Giới hạn cứng của kịch bản bình luận: 600 ký tự (G4).

### 4.2. Recap (đọc khoảng 1–1,5 phút)

Chỉ đọc đoạn recap thì quá ngắn: template recap tiếng Việt chỉ 229–600 ký tự, khoảng 15–40 s. Vì vậy bản đọc ghép thêm các khoảnh khắc then chốt, đúng ý "recap kèm các khoảnh khắc then chốt" của claude.md §2.2:

| Thứ tự | Đoạn | Preset |
|---|---|---|
| 1 | Mở đầu (tĩnh): "Tóm tắt hiệp một." / "Tóm tắt trận đấu." + "Giọng đọc do AI tạo, trên dữ liệu tổng hợp." | `recap` |
| 2 | Mọi đoạn của `RecapText` (đúng ngôn ngữ / giọng của hồ sơ), nghỉ 700 ms giữa các đoạn | `recap` |
| 3 | Câu dẫn (tĩnh): "Những khoảnh khắc đáng chú ý." | `recap` |
| 4 | Mỗi khoảnh khắc: "Phút {display_minute}:" + câu bình luận của khoảnh khắc đó, đúng bản của hồ sơ, theo thứ tự thời gian | Preset của khoảnh khắc (§6.3), giảm một bậc |
| 5 | Kết (tĩnh): "Cảm ơn bạn đã theo dõi cùng Pundit Copilot." | `recap` |

- **Chọn khoảnh khắc:**
  - moment importance 3 trong phạm vi của recap (bàn thắng, VAR huỷ bàn, thẻ đỏ), cộng `key_moment_ids` của recap;
  - **bỏ bàn thắng đã bị VAR huỷ** (giữ câu VAR huỷ bàn), giống cách `RecapPack` của Ngày 3 đếm bàn thắng;
  - bỏ các kind `period_start`, `period_end`, `full_time`, `stat_tick`, `added_time`, `lull` (đã có trong đoạn recap);
  - khử trùng, sắp theo `seq`, tối đa 6 câu (H1) và 8 câu (FULL).
- **"Phút X:" đứng trước mỗi câu**, vì câu casual thường không có phút mà lại nêu tỷ số lúc đó ("Harbor Lions FC 0-1 Kestrel Athletic"). Đọc trong recap mà không có mốc phút thì người nghe dễ hiểu nhầm là tỷ số chung cuộc.
- Câu bình luận lấy từ `director.commentary[f"{moment_id}-c"]`. Moment importance ≥ 2 không bao giờ bị bỏ job (Ngày 3 S5), nên câu luôn có.
- **Độ dài ước tính** (template, 15 ký tự / giây, sẽ hiệu chỉnh ở tầng B):

| Trận | H1 analyst | H1 casual | FULL analyst | FULL casual | Chỉ đọc đoạn recap |
|---|---|---|---|---|---|
| `dominant_loser-42` | 58 s | 43 s | 87 s | 56 s | 16–40 s |
| `comeback_var-7` | 77 s | 53 s | 80 s | 55 s | 19–36 s |
| `red_card_swing-7` | 61 s | 41 s | 88 s | 55 s | 15–39 s |

- **Đã chốt:** analyst khoảng 1–1,5 phút, đạt "1–2 phút" của checklist. Casual khoảng 40–60 s, **ngắn có chủ đích** như recap casual của Ngày 3 (dành cho người mới xem, mỗi câu tối đa một con số). Test giữ biên: analyst FULL 55–120 s, casual ≥ 35 s, mọi recap ≤ 150 s.
- Recap `revision 2` (bản LLM, nếu qua Guardrail) có chữ khác nên ra clip mới; client đang nghe bản cũ thì không bị cắt (§10.6).

### 4.3. Lời chào khi bật giọng

- Văn bản tĩnh: *"Xin chào, đây là giọng đọc do AI tạo của Pundit Copilot. Trận đấu và số liệu đều là dữ liệu tổng hợp."* Bản EN tương ứng (P2).
- Phát ngay trong cú bấm "Bật giọng đọc". Lời chào vừa công bố giọng AI (V12), vừa thử loa, vừa mở khoá autoplay (§10.1).
- Clip lời chào định danh theo nội dung nên chỉ tổng hợp một lần cho mỗi giọng, rồi lưu kho bền.

### 4.4. Ngân hàng câu tĩnh

- `NARRATION_VI` / `NARRATION_EN` gồm lời chào, mở đầu H1 / FULL, câu dẫn khoảnh khắc, kết, mẫu "Phút {m}:" và "Luật {title}:".
- Test: đủ hai ngôn ngữ; không chứa số của trận; không chứa từ cấm của Guardrail; mỗi câu ≤ 120 ký tự; câu VI qua `detect_language` là `vi`.

---

## 5. Chuẩn hoá cách đọc tiếng Việt (`agents/spoken.py`, `agents/spoken_numbers.py`)

### 5.1. Vì sao phải tự chuẩn hoá

Trong các câu sẽ đọc của một trận mẫu (VI, cả hai giọng): 18–25 số thập phân ("xG 0.35"), 9–12 phần trăm, 15–24 cặp số ("0-1", "15–1"), 13–21 lần "xG", 12–36 tên dạng "J. Ellery", tới 41 lần "FC", 2–4 mã đội ("HAR", "KES"), 5–13 cặp ngoặc, 1–3 từ viết hoa kéo dài ("VÀOOO"). Để TTS tự đoán thì có ba rủi ro, cả ba đều là **đọc sai số trên sóng**:
- "2.24" bị đọc như số có dấu phân cách hàng nghìn của vi-VN;
- "0-1" bị đọc thành ngày tháng hoặc phép trừ;
- "J." bị đánh vần thành chữ cái.

Vì vậy số được đổi thành chữ trong code, và từ đó tới TTS không còn chữ số nào (G2).

### 5.2. Tìm số bằng chính bộ tách số của Guardrail

`spoken.normalize(text, language, lexicon)` dùng `agents.numbers.extract_numbers` (Ngày 2) để tìm số, nên **đúng những số Guardrail đã kiểm chứng** là những số được đổi, kèm vai trò của từng số: `percent`, `decimals`, `minute`, `period`, `pair`, `negative`, `word`. Hàm trả về:

```python
@dataclass(frozen=True)
class SpokenNumber:
    written: str           # "0.35"
    spoken: str            # "không phẩy ba mươi lăm"
    value: float           # 0.35 (âm nếu negative)
    start: int             # vị trí của `spoken` trong câu đọc
    end: int

@dataclass(frozen=True)
class Spoken:
    text: str              # câu đọc
    numbers: tuple[SpokenNumber, ...]
```

### 5.3. Bảng đọc số (tiếng Việt)

| Dạng | Viết | Đọc | Quy tắc |
|---|---|---|---|
| Số nguyên | `0`, `5`, `10`, `15` | không, năm, mười, mười lăm | 1–9: một … chín; 10: mười; 11–19: "mười" + đơn vị, 5 → "lăm" |
| Chục | `21`, `24`, `25`, `40`, `57` | hai mươi mốt, hai mươi bốn, hai mươi lăm, bốn mươi, năm mươi bảy | Từ 20: "{chục} mươi" + đơn vị; 1 → "mốt", 5 → "lăm", 4 giữ "bốn" (rõ nghĩa nhất) |
| Trăm, nghìn | `105`, `110`, `1005` | một trăm linh năm, một trăm mười, một nghìn không trăm linh năm | Chục bằng 0 và đơn vị khác 0 → "linh"; hàng trăm bằng 0 bên trong số lớn → "không trăm"; hỗ trợ tới 999 999 |
| Thập phân | `0.35`, `2.08`, `2.73`, `10.4` | không phẩy ba mươi lăm, hai phẩy không tám, hai phẩy bảy mươi ba, mười phẩy bốn | "phẩy"; phần lẻ có số 0 đứng đầu → đọc từng chữ số; còn lại đọc như số nguyên |
| Phần trăm | `57%` | năm mươi bảy phần trăm | `%` → "phần trăm" |
| Cặp số | `15–1`, `1-2` | mười lăm, một / một, hai | Hai số cách nhau bằng dấu phẩy (một nhịp nghỉ ngắn) |
| Khoảng phút | `60-75` (cặp có cờ `minute`) | sáu mươi đến bảy mươi lăm | Cặp mà `extract_numbers` đánh dấu là phút thì nối bằng "đến" |
| Tỷ số kẹp giữa hai tên đội | `Harbor Lions FC 1-2 Kestrel Athletic` | Harbor Lions một, Kestrel Athletic hai | Mẫu `{đội nhà} a-b {đội khách}` theo tên trong `MatchContext`: mỗi số đứng sau tên đội của nó. Thứ tự giá trị không đổi |
| Phút có dấu phẩy trên | `63'`, `45+2'` | phút sáu mươi ba, phút bốn mươi lăm cộng hai | Thêm "phút" chỉ khi số có dấu `'`. "Phút 63" đã có chữ "phút" nên chỉ đổi số |
| Hiệp | `hiệp 1` | hiệp một | Số đếm |
| Thứ tự | `thứ 2`, `thứ 4`, `thứ 1` | thứ hai, thứ tư, thứ nhất | 1 → "nhất", 4 → "tư" |
| Số âm | `-0.42` | âm không phẩy bốn mươi hai | `negative` của `extract_numbers` |
| Số đã viết bằng chữ | `hai cú sút` | giữ nguyên | `word=True`; vẫn được ghi vào `numbers` để guard đối chiếu |

- `spoken_numbers.py` còn có **bộ đọc ngược** `parse_vi_number("hai mươi mốt") → 21`, viết độc lập với bộ sinh (bảng từ khác, thuật toán khác). Guard dùng nó để kiểm tra khứ hồi; property test dùng nó làm oracle.
- Chữ "không" vừa là số 0 vừa là phủ định ("không được công nhận"), nên bộ đọc ngược chỉ đọc trong khoảng `[start, end)` của từng `SpokenNumber`, không quét cả câu.

### 5.4. Từ điển đọc theo trận (`SpokenLexicon.from_match(MatchContext)`)

| Gặp trong câu | Đọc | Nguồn |
|---|---|---|
| Tên đội đầy đủ "Harbor Lions FC" | "Harbor Lions" (bỏ "FC" / "AFC" / "CF" đứng riêng ở cuối) | `TeamRef.name` |
| Mã đội "HAR", "KES", "IRO", "SAL" | Tên đọc của đội ("Harbor Lions", "Kestrel Athletic", …) | `TeamRef.short_name` |
| Tên ngắn "J. Ellery", "M. Ribas" | Họ: "Ellery", "Ribas" | `PlayerRef.short_name` có dạng `X. Họ` |
| Tên cầu thủ Việt "Gia Bảo", "Minh Khôi" | Giữ nguyên | `short_name` không có dạng viết tắt |
| Tên đọc sai khi nghe duyệt | Cách viết theo âm tiếng Việt (ví dụ "Holmgaard" → "Hôm-gat") | Bảng `PRONUNCIATION_VI` (ban đầu rỗng; điền sau V10, mỗi mục một test) |

- Từ điển dựng từ `MatchContext`: đúng đội hình của trận, không đoán bằng regex. Bốn đội hư cấu có 80 cầu thủ; trang nghe duyệt có phần **điểm danh** đọc đủ 80 tên (V10).
- Đổi tên trong **chữ đọc** (không dùng `<sub alias>` của SSML), nên giọng Azure và giọng trình duyệt đọc giống nhau (V4).

### 5.5. Viết tắt, ký hiệu, chữ hoa

| Gặp | Đọc (khởi điểm, chốt sau nghe duyệt V10) |
|---|---|
| `xG` | "ích giê" |
| `PPDA` | "pê pê đê a" |
| `VAR` | "vê a rờ" |
| `km/h` · `km` · `m` (sau số) | "ki lô mét trên giờ" · "ki lô mét" · "mét" |
| `momentum` | giữ nguyên (thuật ngữ analyst; nghe duyệt quyết định có đổi không) |
| Chữ cái lặp từ 3 lần ("VÀOOO", "GOAAAL") | Rút còn một chữ: "Vào", "Goal" |
| Từ viết hoa ≥ 4 chữ cái không nằm trong bảng viết tắt | Viết hoa chữ đầu ("VÀO" → "Vào"), để TTS không đánh vần |
| `(…)` | Thay bằng dấu phẩy: "dứt điểm (xG 0.35), bóng…" → "dứt điểm, ích giê không phẩy ba mươi lăm, bóng…" |
| `–`, `—` giữa hai mệnh đề | Dấu phẩy |
| Ký hiệu khác ngoài chữ, số và dấu câu | Bỏ |

### 5.6. Ví dụ trên câu thật (`dominant_loser-42`, template)

| Viết | Đọc |
|---|---|
| Cơ hội ngon ăn ở phút 9: J. Ellery dứt điểm (xG 0.35), bóng đi chệch khung thành. | Cơ hội ngon ăn ở phút chín: Ellery dứt điểm, ích giê không phẩy ba mươi lăm, bóng đi chệch khung thành. |
| Phút 27, M. Ribas ghi bàn cho Kestrel Athletic với cú dứt điểm xG 0.21. Tỷ số: Harbor Lions FC 0-1 Kestrel Athletic. | Phút hai mươi bảy, Ribas ghi bàn cho Kestrel Athletic với cú dứt điểm ích giê không phẩy hai mươi mốt. Tỷ số: Harbor Lions không, Kestrel Athletic một. |
| VÀOOO! J. Ellery ghi bàn cho Kestrel Athletic! Harbor Lions FC 1-2 Kestrel Athletic. | *(burst)* Vào! *(peak)* Ellery ghi bàn cho Kestrel Athletic! Harbor Lions một, Kestrel Athletic hai. |
| Sau 75 phút: Harbor Lions FC kiểm soát bóng 55%, 16 cú sút, xG 2.08; Kestrel Athletic có 45%, 6 cú sút, xG 0.72. | Sau bảy mươi lăm phút: Harbor Lions kiểm soát bóng năm mươi lăm phần trăm, mười sáu cú sút, ích giê hai phẩy không tám; Kestrel Athletic có bốn mươi lăm phần trăm, sáu cú sút, ích giê không phẩy bảy mươi hai. |
| Nghỉ giữa hiệp với tỷ số Harbor Lions FC 0-1 Kestrel Athletic. xG: HAR 0.48, KES 0.72. | Nghỉ giữa hiệp với tỷ số Harbor Lions không, Kestrel Athletic một. Ích giê: Harbor Lions không phẩy bốn mươi tám, Kestrel Athletic không phẩy bảy mươi hai. |
| Các bàn thắng: M. Ribas (KES, phút 27); T. Calder (HAR, phút 63); J. Ellery (KES, phút 78). | Các bàn thắng: Ribas, Kestrel Athletic, phút hai mươi bảy; Calder, Harbor Lions, phút sáu mươi ba; Ellery, Kestrel Athletic, phút bảy mươi tám. |

Bảng này là **test vàng** đầu tiên. Bộ test vàng đầy đủ được sinh từ mọi câu `speak`, mọi câu mở đầu theo CLB, mọi bản ngắn của luật và mọi recap của 4 kịch bản × 3 seed (§12.2).

### 5.7. Tiếng Anh (P2, bật bằng `TTS_LANGUAGES=vi,en`)

- Cùng khung, gói ngôn ngữ riêng:
  - số → chữ EN ("nought point three five" hoặc "zero point three five", chốt khi nghe duyệt);
  - tỷ số kẹp tên đội ("Harbor Lions one, Kestrel Athletic two");
  - "63'" → "the sixty-third minute";
  - viết tắt ("x G", "V A R").
- Có bộ đọc ngược EN cho guard. Giọng: `en-GB-SoniaNeural` / `en-GB-RyanNeural` (giọng Anh hợp không khí Premier League; chốt khi bật).
- Chưa bật thì hồ sơ EN có `voice` nhận `speech: null`; Fan EN không có nút giọng.

---

## 6. Prosody và SSML (`agents/prosody.py`, `agents/ssml.py`)

### 6.1. Ràng buộc đã xác minh (Microsoft Learn, 22/10/2026)

| Hạng mục | Ràng buộc | Hệ quả cho thiết kế |
|---|---|---|
| `prosody rate` | 0,5–2 lần tốc độ gốc; nhận `+10%`, `-5%` hoặc hệ số | Mọi preset nằm trong 0,94–1,20 |
| `prosody pitch` | 0,5–1,5 lần; nhận `%`, `st`, `Hz` | Preset nằm trong 0,97–1,18 |
| `prosody volume` | 0–100, **mặc định là 100 (to nhất)** | Không tăng âm lượng được, chỉ giảm. Câu bình thường giảm nhẹ để bàn thắng nổi lên |
| `emphasis` | Chỉ có ở `en-US-GuyNeural`, `en-US-DavisNeural`, `en-US-JaneNeural` | **Không dùng.** Nhấn giọng bằng rate, pitch, volume, `break` |
| `mstts:express-as` | Chỉ một số giọng có style; style sai thì cả phần tử bị bỏ qua | **Không dùng** (tầng B kiểm tra `StyleList` của giọng vi-VN qua `/voices/list`) |
| `<lang xml:lang>` | Giọng không phải multilingual không hỗ trợ | Không đổi giọng đọc cho tên nước ngoài; dùng từ điển đọc (§5.4) |
| Số thẻ `voice` / `audio` | Tối đa 50 mỗi SSML | Mỗi cue dùng đúng một `voice` |
| Độ dài audio | Tối đa 10 phút mỗi request | Recap ≤ 150 s |
| Tính phí | Mọi ký tự trong SSML trừ thẻ `<speak>` và `<voice>`, **kể cả markup** (`prosody`, `break`) | SSML gọn: một `prosody` cho mỗi đoạn, không lồng |
| Hạn mức F0 / S0 | F0: 20 request / 60 s, không chỉnh được. S0: 30 request / giây mặc định | `TTS_MAX_RPM` mặc định 18 (an toàn cho F0); đổi khi lên S0 |
| 429 | Phần lớn do năng lực của một giọng ở một region, không phải do quota; cần xử lý thử lại | `RateGate` tôn trọng `Retry-After`; câu live không thử lại (§8.5) |

### 6.2. Bảng preset

| Preset | Dùng cho | rate | pitch | volume |
|---|---|---|---|---|
| `calm` | `excitement` 0: mốc 15', bù giờ, đầu / hết hiệp, hết trận | +0 % | +0 % | −15 % |
| `lively` | `excitement` 1: đợt ép sân, momentum đổi chiều, cú sút cực mạnh | +6 % | +4 % | −8 % |
| `excited` | `excitement` 2: cơ hội lớn, thẻ đỏ, bàn thắng thường | +12 % | +8 % | +0 % |
| `peak` | `excitement` 3: bàn gỡ hoà, bàn vươn lên dẫn, bàn từ phút 80 | +16 % | +12 % | +0 % |
| `burst` | Câu cảm thán đầu của `peak` / `excited` ("Vào!", "Suýt nữa!") | +20 % | +18 % | +0 % |
| `tension` | VAR huỷ bàn (`goal_overturned`), cue `var_review` | −4 % | −3 % | +0 % |
| `explainer` | Lời giải thích luật cho Người mới xem | −6 % | +0 % | −10 % |
| `recap` | Đoạn recap, câu dẫn | −2 % | +0 % | −10 % |
| `greeting` | Lời chào | +0 % | +2 % | −10 % |

- Khoảng nghỉ: sau burst 250 ms; trước phần chính 150 ms; trước lời giải thích 600 ms; giữa các đoạn recap 700 ms. Mọi khoảng nghỉ ≤ 1 000 ms.
- Đây là giá trị khởi điểm, chốt sau nghe duyệt (V10). Bảng là dữ liệu (`PRESETS`), có test vàng; sửa bảng không phải sửa code.

### 6.3. Chọn preset

`preset_for(kind, excitement)`:
1. Có `KIND_PRESETS[kind]` thì dùng (`goal_overturned` và `var_review` → `tension`).
2. Không có thì dùng `BY_EXCITEMENT[excitement]` = `calm` / `lively` / `excited` / `peak`.

Preset của khoảnh khắc trong recap hạ một bậc (`peak` → `excited`, `excited` → `lively`, các preset khác giữ nguyên): recap kể lại, không phải trực tiếp.

### 6.4. Câu cảm thán đầu (burst)

- Áp dụng khi preset chính là `peak` hoặc `excited`, câu đầu của phần chính kết thúc bằng "!" và có tối đa 4 từ. Câu đó tách thành đoạn `burst`, nghỉ 250 ms, rồi tới phần còn lại.
- Đây là cách "nhấn giọng" ở bàn thắng khi giọng vi-VN không có `emphasis`.
- Ví dụ: "VÀOOO! Ellery ghi bàn…" → *Vào!* (rate +20 %, pitch +18 %) · nghỉ · *Ellery ghi bàn…* (peak).

### 6.5. SSML

```xml
<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="vi-VN">
  <voice name="vi-VN-HoaiMyNeural">
    <prosody rate="+20%" pitch="+18%">Vào!</prosody><break time="250ms"/>
    <prosody rate="+16%" pitch="+12%">Ellery ghi bàn cho Kestrel Athletic! Harbor Lions một, Kestrel Athletic hai.</prosody>
    <break time="600ms"/>
    <prosody rate="-6%" volume="-10%">Luật bù giờ: …</prosody>
  </voice>
</speak>
```

- `build_ssml(cue_segments, voice)`:
  - chữ được escape (`&`, `<`, `>`);
  - giá trị thuộc tính chỉ lấy từ bảng preset, không bao giờ từ văn bản;
  - bỏ thuộc tính có giá trị +0 % để SSML gọn (V6, tính phí).
- `check_ssml(ssml)` (chạy trong G5):
  - parse được bằng `xml.etree.ElementTree`;
  - chỉ có các phần tử `speak`, `voice`, `prosody`, `break` và các thuộc tính trong danh sách cho phép;
  - đúng một `voice`;
  - `xml:lang` khớp ngôn ngữ của giọng;
  - ≤ 10 KB.
- `clip_id = sha256(f"v1|{provider}|{voice}|{output_format}|{ssml}").hexdigest()[:32]`. Có `provider` trong khoá nên đổi mock ↔ Azure không bao giờ phục vụ nhầm file WAV thành MP3.

### 6.6. Ánh xạ sang giọng trình duyệt

- `SpeechSegment.rate`, `pitch`, `volume` là hệ số: `1 + phần_trăm / 100`, ví dụ `peak` → 1,16 · 1,12 · 1,0. Hệ số này gán thẳng vào `SpeechSynthesisUtterance` (rate 0,1–10, pitch 0–2, volume 0–1).
- `pause_before_ms` được client chờ giữa hai utterance.
- Cùng kịch bản, cùng nhịp với SSML (V4). Giọng trình duyệt không có cùng âm sắc, nhưng đọc cùng chữ và cùng số.

---

## 7. SpeechGuard (`agents/speech_guard.py`)

Chạy cho mọi cue **trước** khi trả `AudioRef` hay gửi kịch bản cho trình duyệt. Tất định, khoảng 1 ms mỗi cue.

| # | Kiểm tra | Trượt thì |
|---|---|---|
| G1 | Mọi đoạn có nguồn thuộc bảng §4 (model có kiểu; không có đường nhận chuỗi tự do) | Lỗi lập trình: test chặn; lúc chạy ghi `guard` |
| G2 | **Khứ hồi số:** (a) tiếng Việt: câu đọc không còn chữ số ASCII; (b) số `SpokenNumber` bằng số con số mà `extract_numbers` tìm thấy trong chữ viết; (c) mỗi `SpokenNumber` đọc ngược ra đúng `value`; (d) thứ tự giá trị giữ nguyên | `guard` |
| G3 | Ngôn ngữ của câu đọc (bỏ tên riêng) khớp ngôn ngữ của giọng (`detect_language`) | `guard` |
| G4 | Độ dài: bình luận ≤ 600 ký tự, recap ≤ 3 000, lời chào ≤ 200; tối đa 16 đoạn | `guard` |
| G5 | SSML hợp lệ (`check_ssml`) | `guard` |
| G6 | Không có từ cấm của Guardrail (Ngày 2) trong câu đọc cuối cùng (từ điển đọc và câu dẫn được ghép thêm vào) | `guard` |
| G7 | Recap: mọi câu khoảnh khắc thuộc phạm vi của recap (`seq ≤ recap.as_of_seq`; H1 chỉ hiệp 1) | `guard` |

- Trượt guard: `speech.note = "guard"`, không có audio, **trình duyệt cũng không đọc**. Bộ đếm `speech.guard_rejected` tăng, log có mã lỗi và `item_id`, span của Narrator có `status=rejected`.
- Mục tiêu: 0 lần trượt trên dữ liệu thật (test quét 4 × 3 trận). Một lần trượt là một bug của bộ chuẩn hoá, sửa bằng luật mới kèm test.

---

## 8. Dịch vụ giọng đọc (`speech/`)

### 8.1. Port

```python
class SynthesisRequest(BaseModel):          # frozen
    clip_id: str; ssml: str; voice: str; lang: str
    kind: Literal["commentary", "recap", "greeting"]
    priority: int                           # 0–3: excitement; recap 2; greeting 3
    billable_chars: int                     # len(ssml) trừ thẻ <speak>, <voice>
    estimate_ms: int

class SynthesizedAudio(BaseModel):
    data: bytes; media_type: Literal["audio/mpeg", "audio/wav"]; duration_ms: int
    provider: Literal["mock", "azure"]; request_id: str | None

class Synthesizer(Protocol):
    name: Literal["mock", "azure"]
    media_type: Literal["audio/mpeg", "audio/wav"]
    async def synthesize(self, request: SynthesisRequest, *, timeout_s: float) -> SynthesizedAudio: ...
    async def aclose(self) -> None: ...
```

Lỗi có kiểu: `SpeechTimeout`, `SpeechThrottled(retry_after_s)`, `SpeechAuthError`, `SpeechBadRequest`, `SpeechUnavailable`, `BadAudio`. Lỗi của port không bao giờ thành `5xx` của API ngoài `/audio` (V3).

### 8.2. `MockSynthesizer`

- WAV PCM 16 kHz mono 16-bit. Độ dài = `estimate_ms × MOCK_TTS_TIME_SCALE`; mỗi đoạn mở đầu bằng 80 ms tiếng 440 Hz biên độ thấp, phần còn lại là im lặng. Nội dung tất định nên cùng request ra cùng byte.
- `estimate_ms = Σ len(đoạn) / (15 ký tự/giây × rate) + Σ khoảng nghỉ`. Hằng số 15 là giả định, hiệu chỉnh ở tầng B bằng `WordsPerMinute` của giọng (`/voices/list`) và số đo thật.
- `MOCK_TTS_LATENCY_MS` (độ trễ); `MOCK_TTS_FAULTS`:
  - `kind:n`: n lần gọi tiếp theo lỗi kiểu `kind`;
  - `kind` không có số: lỗi mãi;
  - các kiểu `error`, `timeout`, `throttle`, `bad_audio`, `auth`.

  Cú pháp và cách parse giống `MOCK_LLM_FAULTS`.

### 8.3. `AzureSynthesizer` (REST, `httpx.AsyncClient` dùng chung)

| Hạng mục | Giá trị |
|---|---|
| Endpoint | Key: `https://{SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1`. Entra: endpoint custom domain (đường dẫn TTS trên custom domain **xác minh ở tầng C**; dự kiến `/tts/cognitiveservices/v1`, cùng gốc với `/tts/cognitiveservices/voices/list`) |
| Header | `Content-Type: application/ssml+xml` · `X-Microsoft-OutputFormat: audio-24khz-48kbitrate-mono-mp3` · `User-Agent: pundit-copilot/0.5` (bắt buộc, < 255 ký tự) · xác thực (§8.4) |
| Timeout | Kết nối 2 s; tổng `TTS_TIMEOUT_S` (bình luận) hoặc `TTS_RECAP_TIMEOUT_S` |
| Kết nối | Keep-alive; tối đa 8 kết nối, 4 kết nối chờ |
| Kiểm tra kết quả | `200`, `Content-Type: audio/mpeg`, ≥ 1 KB, bắt đầu bằng `ID3` hoặc khung MP3 (byte `0xFF`, byte sau có 3 bit cao bằng 1). Sai thì `BadAudio` |
| Thời lượng | CBR 48 kbit/s nên `duration_ms ≈ (bytes − ID3) / 6`. Lệch ước tính quá 0,3–3 lần thì đếm `duration_mismatch` (dấu hiệu audio bị cắt) |

| Mã HTTP | Lỗi | Xử lý |
|---|---|---|
| 400, 415 | `SpeechBadRequest` | Bug của ta (SSML, header). Log `clip_id` + hash SSML; breaker đếm lỗi; test phải chặn trước |
| 401, 403 | `SpeechAuthError` | Entra: làm mới token một lần rồi thử lại. Vẫn lỗi thì `breaker.trip(300 s)`, `/healthz` báo `auth_error` |
| 429 | `SpeechThrottled` | `Retry-After` (mặc định 5 s) → `RateGate.cooldown`; breaker đếm lỗi |
| 5xx, lỗi mạng | `SpeechUnavailable` | Breaker đếm lỗi |
| Hết giờ | `SpeechTimeout` | Breaker đếm lỗi |

- Thử lại: câu bình luận **không** thử lại (thử lại làm lỡ thời điểm, giống bình luận LLM của Ngày 3); recap và lời chào thử lại tối đa 2 lần, sau cooldown.
- Không bao giờ log header, key, token. Có `X-RequestId` trong phản hồi thì log để tra cứu.

### 8.4. Xác thực (`speech/auth.py`)

| Chế độ | Khi nào | Header |
|---|---|---|
| Key | Dev, tầng B | `Ocp-Apim-Subscription-Key: <SPEECH_KEY>` (dùng được với mọi kiểu endpoint) |
| Entra ID | Azure (Managed Identity), hoặc local sau `az login` | `Authorization: Bearer aad#{SPEECH_RESOURCE_ID}#{token}`; scope `https://cognitiveservices.azure.com/.default` |

- Entra cần resource có **custom subdomain** (bật rồi không đổi lại được) và vai trò **Cognitive Services Speech User** cho identity. Bicep phải tạo đủ (§17).
- Token lấy bằng `DefaultAzureCredential` đồng bộ trong `asyncio.to_thread`, cache tới 5 phút trước khi hết hạn; nhiều lần gọi đồng thời chỉ lấy một token (khoá async).

### 8.5. `RateGate` (`speech/gate.py`)

- **Token bucket:** sức chứa `TTS_MAX_RPM`, nạp `TTS_MAX_RPM / 60` token mỗi giây.
- **Semaphore:** `TTS_MAX_CONCURRENCY` request đồng thời.
- **Ưu tiên:** request chờ trong heap theo `(priority giảm dần, thứ tự đến)`. Thời gian chờ tối đa:

  | Request | Chờ tối đa |
  |---|---|
  | Bình luận `excitement` 0–1 | 1,5 s |
  | Bình luận `excitement` 2–3 | 3 s |
  | Lời chào | 5 s |
  | Recap | 60 s |

  Quá hạn thì `throttled`.
- **Cooldown:** khi gặp 429, không phát request nào cho tới hết `Retry-After`.
- Đồng hồ tiêm vào; test chạy không chờ thật.

### 8.6. Breaker, ngân sách ký tự, tốc độ replay

- **Breaker riêng của giọng đọc** (`CircuitBreaker` của Ngày 2): 3 lỗi liên tiếp thì mở 60 s; lỗi xác thực thì mở ngay 300 s. Breaker này tách khỏi breaker của claim và của studio: Azure Speech hỏng không ảnh hưởng LLM, và ngược lại.
- **Ngân sách ký tự:** cộng `billable_chars` của các lần tổng hợp thật (cache hit không tính), theo ngày UTC. Hết `TTS_DAILY_CHAR_BUDGET` thì chỉ phục vụ clip đã có, clip mới nhận `note=budget`. Đây là lưới an toàn của app; lưới chính vẫn là budget alert trên Azure và hạn mức miễn phí của F0.
- **Tốc độ replay:** phiên chạy nhanh hơn `TTS_MAX_SPEED` thì cue có `note=speed` (không ai nghe kịp ở 1000x).
- **Ước tính lưu lượng:**
  - mỗi trận có 25–35 câu `speak`, khoảng 5–8 nghìn ký tự tính phí cho mỗi hồ sơ có giọng (gồm markup, lời giải thích luật và hai recap);
  - ở 20x, một trận kéo dài khoảng 4,7 phút, tức 6–8 request mỗi phút cho mỗi hồ sơ có giọng. F0 (20 request / 60 s) đủ cho hai hồ sơ có giọng cùng lúc;
  - cache theo nội dung làm lần chạy lại cùng kịch bản trên đường template không tốn thêm.

### 8.7. `Narrator` (`speech/narrator.py`)

```python
class Narrator:
    def cue(self, script: SpeechScript, *, gender, lang, kind, priority, item_id,
            session_speed: float, trace_offset_ms: float = 0) -> SpeechCue: ...
    async def wait(self, clip_id: str, timeout_s: float) -> ClipFile | ClipFailure | None: ...
    async def produce_now(self, request) -> ClipFile:       # CLI `tts warm`, recap REST
    def spans_of(self, item_id: str) -> tuple[TraceSpan, ...]: ...
    def stats(self) -> dict[str, Any]: ...                   # /healthz
    async def aclose(self) -> None: ...                      # tắt app: huỷ task, đóng httpx
```

`cue()` chạy đồng bộ trong vòng lặp sự kiện:
1. Dựng chữ đọc, prosody, SSML và chạy SpeechGuard. Trượt thì `note=guard`.
2. `TTS_PROVIDER=browser` → `note=server_off`.
3. Clip đã có trong `ClipCache` → `AudioRef(exact=True)`.
4. Clip đang được tạo (single-flight) → `AudioRef(exact=False)`.
5. Breaker mở → `circuit_open`; hết ngân sách → `budget`; replay quá nhanh → `speed`.
6. Còn lại: tạo task nền `_produce`, đăng ký vào `_inflight[clip_id]` (mỗi clip một task, mọi hồ sơ dùng chung), trả `AudioRef(exact=False)`.

`_produce(request)`:
1. Tra `DurableStore` (nếu có) → chép vào cache.
2. `RateGate.acquire(priority, max_wait)`.
3. `synthesizer.synthesize(...)`.
4. Kiểm tra audio → `ClipCache.put` (ghi nguyên tử).
5. Clip Azure, lời chào và recap: `DurableStore.put` (lỗi ở bước này chỉ ghi log, không làm hỏng clip).
6. Ghi số đo, span; báo thành công hoặc thất bại cho breaker. Thất bại được nhớ 60 s, để `GET` trả `503` ngay thay vì chờ.

- `_inflight` gỡ entry khi task xong.
- Tắt app: huỷ mọi task và chờ chúng kết thúc. Test chống rò rỉ task của Ngày 2–3 được mở rộng sang Narrator.

### 8.8. Lưu trữ

| Kho | Nội dung | Chi tiết |
|---|---|---|
| `ClipCache` (`AUDIO_CACHE_DIR`) | Mọi clip đã tạo | File `{clip_id}.{mp3\|wav}` + chỉ mục trong bộ nhớ (kích thước, loại, thời lượng, lần dùng cuối). Ghi vào file tạm cùng thư mục rồi `os.replace` (người đọc không bao giờ thấy file dở). LRU khi vượt `AUDIO_CACHE_MAX_MB`; không xoá clip được dùng trong 10 phút gần nhất (trừ khi vượt 1,5 lần giới hạn). Khởi động: quét thư mục, dựng lại chỉ mục, xoá file tạm sót lại |
| `LocalStore` (`AUDIO_STORE=local`, mặc định khi chưa có Azure) | Clip Azure, recap, lời chào | `{clip_id}.{ext}` + `{clip_id}.json` (metadata). Còn sau khi khởi động lại |
| `BlobStore` (`AUDIO_STORE=blob`) | Như trên, trên Azure | Container **private** `audio`, blob `clips/{clip_id}.{ext}`; `content_type`, `cache_control` bất biến; metadata `kind`, `voice`, `preset`, `match_id`, `scope`, `chars`, `duration_ms`, `provider`. `overwrite=False`; blob đã tồn tại tính là thành công (idempotent). Client đồng bộ trong `asyncio.to_thread` |

- **Phục vụ qua API, không dùng SAS, không mở public blob:** cùng origin, không cần CORS; Managed Identity là đủ; không có URL hết hạn.
- **Recap "lưu vào Blob Storage":** sau khi tổng hợp xong, recap được ghi `clips/{clip_id}.mp3` kèm metadata `scope=H1|FULL`, `match_id`. Đây là bằng chứng dùng Azure Storage trên bản deploy.
- **Khởi động lại:** template tất định nên recap và bình luận dựng lại ra cùng `clip_id`; clip được lấy từ kho bền, không tổng hợp lại.

### 8.9. Trace, log, số đo

- **Span của Narrator** cho mỗi clip (agent `Narrator`): `narrate` (kind `agent`: preset, số đoạn, ký tự, 300 ký tự đầu của SSML), `speech_guard` (kind `guardrail`: ok / rejected + mã), `tts.synthesize` (kind `tool`: `cache=hit|durable|miss`, ms, bytes, `duration_ms`, provider, `request_id`), `fallback` khi có `note`.
- Span lưu theo `item_id` (giới hạn 300 item). `GET /matches/{id}/studio/{item_id}` gộp span của job studio với span của Narrator. `start_ms` của Narrator cộng thêm điểm kết thúc của job, nên trên Gantt các span này nằm sau StudioPublisher.
- **Log có cấu trúc**, mỗi lần tổng hợp một dòng: `speech.synthesize clip_id voice preset kind chars ms status cache provider match_id item_id`. Không log nội dung câu, key hay token.
- **Số đo cho `/healthz`:** `synthesized`, `cache_hits`, `durable_hits`, `throttled`, `guard_rejected`, `errors{kind}`, `chars_today`, `latency_ms_p95`, `queue`, `breaker`.

---

## 9. API và tích hợp phiên

### 9.1. Render và tổng hợp trước trong `MatchSession`

- `_publish_commentary`:
  - khoá render của Ngày 3 là `(profile.key(), seen)`, và `voice` đã nằm trong `profile.key()`;
  - với hồ sơ có `voice` và `view.speak`, gọi `narrator.cue(..., session_speed=self.speed)` một lần cho mỗi khoá;
  - kết quả gắn vào `view.speech` trước khi `model_dump`. Hai client cùng khoá dùng chung payload, như Ngày 3.
- `client_state` (`hello` / `resync`): `speech=None` cho mọi mục (V5). Có test: `hello` 30 mục với hồ sơ có giọng → 0 lần tổng hợp.
- `publish_studio(Recap)`: khoá render đổi từ `(language, mode)` thành `(language, mode, voice)`; hồ sơ có giọng nhận cue recap với `kind="recap"`, `priority=2`.
- Dừng phiên **không** huỷ task tổng hợp: clip dùng chung giữa các phiên và người nghe. Tắt app thì huỷ (`Narrator.aclose`).

### 9.2. `GET` / `HEAD /audio/{clip_id}.{ext}` (`api/audio.py`)

1. Kiểm tra `clip_id` khớp `^[0-9a-f]{32}$` và `ext` thuộc `mp3`, `wav`. Sai thì `422` (chặn path traversal).
2. Có trong `ClipCache` thì trả `FileResponse`. Starlette 1.9 (đang cài) hỗ trợ `Range` → `206` / `416`, cùng `ETag` và `Accept-Ranges`. Header: `Cache-Control: public, max-age=31536000, immutable`; `Content-Type` theo đuôi; `X-Content-Type-Options: nosniff`.
3. Đang tạo thì `await narrator.wait(clip_id, TTS_GET_WAIT_S)`: xong thì như bước 2; lỗi hoặc quá giờ thì `503 {"detail": "audio_unavailable", "reason": "..."}` kèm `Retry-After: 2` và `Cache-Control: no-store`.
4. Không biết clip: tra `DurableStore` (trường hợp app vừa khởi động lại); có thì chép vào cache rồi trả, không có thì `404`.

`/audio` **không bao giờ tổng hợp văn bản tuỳ ý.** Chỉ clip mà server đã dựng từ nội dung đã kiểm chứng mới tồn tại, nên bản public không thành "dịch vụ TTS miễn phí" cho người ngoài.

### 9.3. Recap, lời chào, danh sách giọng

- `GET /recap?…&voice=female`: render `RecapView` cùng `speech`. Clip chưa có thì tổng hợp ngay (`priority=2`), trả `AudioRef(exact=False)`; client mở URL thì `GET /audio` chờ (recap dưới 2 phút, Azure dự kiến tổng hợp trong vài giây; số đo thật ở tầng B). `409` / `404` giữ nguyên quy ước Ngày 3.
- `GET /speech/greeting`: cue lời chào, có audio (cache vĩnh viễn).
- `GET /speech/voices`: `[{lang: "vi-VN", gender: "female", voice: "vi-VN-HoaiMyNeural", display_name: "Hoài My", server_audio: true}, …]`. `server_audio` là `false` khi provider là `browser` hoặc `off`.

### 9.4. `/healthz`

Thêm khối `speech` (bỏ hẳn khi `TTS_PROVIDER=off`, dùng `response_model_exclude_unset`, giống khối `studio` khi studio tắt):

```
speech: { provider: "mock|browser|azure", languages: ["vi"], voices: [...], store: "none|local|blob",
          breaker: "closed|open|half_open", degraded: ["auth_error"?], synthesized, cache_hits,
          durable_hits, throttled, guard_rejected, errors: {...}, chars_today, char_budget,
          latency_ms_p95, queue, stt: "off|mock|azure" }
```

Chip máy chủ ở trang chọn trận (Ngày 4) hiện thêm provider giọng.

### 9.5. CLI

| Lệnh | Việc |
|---|---|
| `pundit speak --text "…" --lang vi --voice female --preset peak --out a.mp3 [--provider mock\|azure] [--ssml-only]` | Công cụ dev: in câu đọc, SSML, kết quả SpeechGuard (G2–G6; G1 không áp dụng vì là chữ nhập tay); ghi file audio. Không có đường API tương ứng |
| `pundit tts warm --scenario S --seed N [--voices female,male] [--modes casual,analyst] [--beginner] [--provider azure]` | Chạy studio offline (như `pundit studio`), dựng cue cho mọi câu `speak`, recap và lời chào, tổng hợp vào kho bền. In số clip, số ký tự, chi phí ước tính. Dùng để "làm ấm" cache cho kịch bản demo trước giờ chấm |
| `pundit recap … --audio out.mp3 --voice female` | Ghi audio của bản đọc recap |

### 9.6. Bật, tắt và tương thích

- `TTS_PROVIDER=off`:
  - không dựng Narrator, không mount router `audio`;
  - `voice` trong query vẫn được nhận (Fan không rơi vào vòng `4422`) nhưng `speech` luôn là `null`;
  - `/healthz` không có khối `speech`.
- `STUDIO_ENABLED=false`: như Ngày 3, không có bình luận nên không có giọng.
- Mọi test Ngày 1–4 chạy lại với `TTS_PROVIDER=off` và với mặc định `mock` (hồ sơ không có `voice`); cả hai phải xanh.

---

## 10. Fan VN Player: phát audio

### 10.1. Bật giọng và mở khoá autoplay

1. Header có nút **🔊 Bật giọng đọc** (mặc định tắt). Lựa chọn giọng nữ / nam được nhớ trong hồ sơ; trạng thái bật thì **không** tự bật lại khi tải trang, vì trình duyệt cần cử chỉ người dùng. Nếu lần trước đang bật, trang hiện nút "Chạm để bật lại giọng đọc".
2. Trong chính handler của cú bấm (`unlock.ts`):
   - phát một clip WAV im lặng 50 ms (data URI) trên phần tử `<audio>` duy nhất của trang;
   - gọi `speechSynthesis.speak(new SpeechSynthesisUtterance(""))` để mở khoá cả giọng trình duyệt (iOS Safari cần);
   - gọi `GET /speech/greeting` rồi phát lời chào.
3. Sau đó lưu `profile.voice` và mở kết nối WS mới với `voice=…`. `hello` không chứa audio; câu live đầu tiên có `speech` sẽ được đọc.
4. `play()` bị từ chối (`NotAllowedError`) thì chuyển sang trạng thái `needs_gesture` và hiện banner "Chạm để bật âm thanh".

### 10.2. Nhận message live

- `useMatchConnection(…, { onLive })` gọi `onLive(message)` **đồng bộ** cho từng message mà socket đã chấp nhận (đúng `n`, không trùng), trước khi gom khung hình. Audio không phụ thuộc lịch render của store.
- `useCommentaryAudio`:
  - `hello` / `resync` → `scheduler.reset({keepCurrent: true})`: bỏ hàng đợi cũ, câu đang đọc thì đọc nốt;
  - `commentary` có `speech` → `enqueue`;
  - `status` → `pause` / `resume`; `status.restarted` → `clear`.

### 10.3. Bộ lập lịch (`lib/audio/scheduler.ts`, hàm thuần)

`step(state, input, now) → { state, effects }`

- Input: `enqueue(view)`, `ended(id)`, `failed(id, reason)`, `tick`, `pause`, `resume`, `mute`, `unmute`, `recapStart`, `recapEnd`, `reset`, `clear`.
- Effect: `play(item, source: "server" | "browser")`, `stop(id)`.

| # | Luật |
|---|---|
| R1 | Chỉ nhận mục có `speech`, chưa thấy `id` (nhớ 100 `id` gần nhất) và đang không `mute`. Bỏ mục có `note` ∈ {`guard`, `speed`} |
| R2 | **Chen ngang:** mục mới `excitement` 3, mục đang đọc `excitement` ≤ 1 và đã đọc ≥ 1 s → `stop` mục đang đọc (tính là bị chen) và phát ngay mục mới |
| R3 | Hàng đợi tối đa `MAX_WAITING` (3). Đầy thì giữ theo `keepForAudio` của Ngày 4: phấn khích nhất, rồi mới nhất |
| R4 | **Quá hạn:** bỏ mục chờ quá 6 s / 8 s / 12 s / 20 s (theo `excitement` 0 / 1 / 2 / 3), tính theo đồng hồ thật kể từ lúc nhận |
| R5 | Rảnh thì chọn: mục `excitement` 3 đầu tiên nếu có, không thì mục đến sớm nhất. Câu được đọc đúng thứ tự trận, trừ bàn thắng |
| R6 | Nguồn phát: `browser` nếu `speech.audio` là `null` (note `server_off` / `circuit_open` / `throttled` / `budget`) hoặc đang trong thời gian chỉ dùng trình duyệt (R8); còn lại `server` |
| R7 | `ended`: câu tiếp theo theo R5 |
| R8 | `failed` từ nguồn `server`: thử lại **ngay** bằng `browser` nếu còn trong hạn R4. 3 lần lỗi server liên tiếp thì chỉ dùng trình duyệt trong 60 s (breaker phía client) |
| R9 | `pause` (trận tạm dừng): câu đang đọc đọc nốt, không bắt đầu câu mới. `resume`: áp R4 rồi R5 |
| R10 | `recapStart`: `stop` câu đang đọc, giữ hàng đợi. `recapEnd`: như `resume` |
| R11 | `mute`: `stop`, xoá hàng đợi. `clear` (restart, đổi trận): như `mute` nhưng giữ trạng thái bật |
| R12 | Trình duyệt không có giọng cho ngôn ngữ đó: mục cần `browser` bị bỏ và đếm; chip báo "Không có giọng dự phòng" |

**Bất biến (property test bằng fast-check):**
- I1: không bao giờ có hai `play` mà giữa chúng thiếu `ended`, `failed` hoặc `stop` (tối đa một tiếng).
- I2: hàng đợi luôn ≤ 3.
- I3: mỗi `id` được phát tối đa một lần (kể cả khi chuyển sang trình duyệt sau lỗi).
- I4: khi `mute` thì không có `play`.
- I5: mục `excitement` 3 không bị bỏ vì đầy hàng đợi khi vẫn còn giữ mục `excitement` thấp hơn.
- I6: không mục nào bắt đầu sau khi đã quá hạn R4.
- I7: thứ tự phát theo thứ tự đến, trừ R2 và R5.
- I8: ở `pause` không có `play` mới.

### 10.4. Phát: `AudioPlayer` và `BrowserVoice`

- **`AudioPlayer`:**
  - một `HTMLAudioElement` cho cả trang, đã mở khoá ở §10.1;
  - `play(url)` = đặt `src` rồi gọi `play()`;
  - sự kiện `ended` → `ended`; sự kiện `error`, lỗi `play()` hoặc 12 s không bắt đầu phát → `failed`;
  - URL ghép với `NEXT_PUBLIC_API_BASE` (rỗng trên bản build, `http://localhost:8000` khi `npm run dev`);
  - khi một mục vào hàng đợi, `fetch(url)` với độ ưu tiên thấp để làm ấm cache HTTP (clip bất biến).
- **`BrowserVoice`:**
  - chọn giọng theo thứ tự: tên chứa `HoaiMy` / `NamMinh` theo giới tính → mọi giọng có `lang` bắt đầu bằng `vi` → không có;
  - chờ sự kiện `voiceschanged` tối đa 1 s;
  - mỗi đoạn là một utterance (đoạn dài hơn 200 ký tự thì tách theo câu, tránh lỗi Chrome dừng utterance dài khoảng 15 s);
  - chờ `pause_before_ms` giữa các utterance;
  - `stop` = `speechSynthesis.cancel()`.
- **Kỳ vọng (xác minh ở G0):** Edge trên Windows có "Microsoft HoaiMy Online (Natural) - Vietnamese (Vietnam)" và "Microsoft NamMinh Online (Natural) …" trong `speechSynthesis.getVoices()` (cần mạng). Khi đó giọng dự phòng nghe gần như giọng Azure.

### 10.5. Giao diện

- **`VoiceControl`** (header của Fan):
  - nút bật / tắt (`aria-pressed`);
  - menu giọng **Nữ · Hoài My / Nam · Nam Minh** (P1);
  - chip nguồn giọng: "Giọng AI · Azure" (provider `azure`), "Giọng thử nghiệm" (provider `mock`), "Giọng dự phòng của trình duyệt", "Chưa nghe được giọng đọc — đang hiện chữ";
  - nút **Bỏ qua** câu đang đọc.
- **`ProfileSetup`**: thêm "Giọng đọc" (Tắt / Nữ / Nam) trong phần nâng cao. Chọn giọng ở đây chỉ lưu sở thích; vẫn cần bấm bật ở header (§10.1).
- **Thẻ đang được đọc** có `data-speaking="true"`: viền nổi bật và biểu tượng sóng âm (tắt chuyển động khi `prefers-reduced-motion`). Thẻ này đóng vai phụ đề của câu đang đọc.
- **Không tự cuộn** khi người dùng đang đọc lại phía dưới, giữ hành vi Ngày 4.
- Chữ giao diện VI / EN theo `i18n` (khoá `fan.voice*`, `fan.recapListen*`, `studio.mic*`). Thiếu khoá thì lỗi biên dịch (Ngày 4).

### 10.6. Recap audio (`RecapAudio` trong tab Tóm tắt)

- Nút **▶ Nghe tóm tắt (≈ 1:25)**: thời lượng lấy từ `audio.duration_ms` (ước tính tới khi có file thật). Bấm thì gọi `GET /recap?…&voice=` rồi phát.
- Trình phát `<audio controls preload="none">`: tua (Range / `206`), tạm dừng; nút **Nghe lại** đưa về 0 rồi phát.
- Trạng thái: chuẩn bị (spinner "Đang chuẩn bị audio…") → đang phát / tạm dừng → hết (hiện "Nghe lại").
- Server không có audio (`note`): nút **Nghe bằng giọng trình duyệt** (không tua được); không có giọng thì ẩn nút.
- Khi recap phát: `scheduler.recapStart()`; dừng hoặc hết: `recapEnd()` (R10).
- Có `revision 2` trong lúc đang nghe bản 1: không cắt; hiện "Có bản cập nhật — nghe bản mới".

### 10.7. Trạng thái máy đọc được

`window.punditAudio = {mode, source, current: {id, source, startedAt}, waiting: [ids], played: [ids, 50 gần nhất], dropped, preempted, failed, fallbacks, needsGesture, maxConcurrent}`, giống `window.punditOverlay` của Ngày 4. E2E và panel debug (`?debug=1`) đọc trực tiếp từ đây.

### 10.8. Khả năng truy cập

- Giọng đọc là lớp thêm; mọi nội dung vẫn có dạng chữ.
- Vùng "đang đọc" có `aria-live="off"`, để trình đọc màn hình không đọc chồng lên giọng của app.
- Mọi nút có nhãn và dùng được bằng bàn phím: `V` bật / tắt giọng, `N` bỏ qua câu (tắt khi đang gõ).
- axe: 0 vi phạm mức serious / critical.

---

## 11. Speech-to-Text (P2: làm cuối, cắt đầu tiên)

| Hạng mục | Thiết kế |
|---|---|
| UI | Nút 🎤 cạnh ô claim của Studio, chỉ hiện khi `STT_PROVIDER` khác `off`, có `navigator.mediaDevices` và đang ở secure context (localhost hoặc HTTPS). Bấm để bắt đầu, bấm lần nữa để dừng, tự dừng sau `STT_MAX_SECONDS` (15 s), `Esc` để huỷ. Có đồng hồ đếm và chấm đỏ khi đang ghi |
| Ghi âm (`lib/audio/recorder.ts`, tải động) | `getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}})` → `AudioContext({sampleRate: 16000})` → AudioWorklet (`public/audio/pcm-worklet.js`) → Int16 → WAV PCM 16 kHz mono (≤ 15 s ≈ 480 KB). Trình duyệt không nhận `sampleRate` thì nội suy tuyến tính trong JS. Studio tải lần đầu không tăng dung lượng JS |
| API | `POST /speech/recognize?lang=vi\|en`, `Content-Type: audio/wav`. Ngôn ngữ theo ngôn ngữ UI của Studio (REST short audio bắt buộc có `language`, không tự nhận diện) |
| Kiểm tra ở server | `Content-Length` ≤ 1 MB (`413`); header RIFF / WAVE, PCM, mono, 16 000 Hz, 16-bit (`422`); độ dài ≤ `STT_MAX_SECONDS` (`422`); sai content type (`415`) |
| Azure | REST short audio: `https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1?language=vi-VN&format=detailed&profanity=masked` (key), hoặc `…/stt/speech/recognition/conversation/cognitiveservices/v1` trên custom domain (Entra). `Content-Type: audio/wav; codecs=audio/pcm; samplerate=16000`; `Accept: application/json`. Audio ≤ 60 s; chỉ trả kết quả cuối |
| Kết quả | `RecognitionStatus`: `Success` → `ok` (`text = NBest[0].Display`, `confidence`); `NoMatch` → `no_match`; `InitialSilenceTimeout` / `BabbleTimeout` → `silence`; `Error` → `error` (`503`) |
| Hạn mức | F0 chỉ cho 1 request STT đồng thời: semaphore 1, chờ tối đa 5 s, quá thì `429` ("đang bận"). Thêm giới hạn 10 request mỗi phút cho cả app (chống lạm dụng trên bản public) |
| Mock | `MockRecognizer` vẫn kiểm tra WAV như thật, trả `MOCK_STT_TEXT` (VI: "Đội nhà áp đảo hiệp 2"; EN: "The home side dominated the second half") |
| An toàn | Chữ nhận được **chỉ điền vào ô claim**; người dùng kiểm tra rồi tự bấm Enter (con người quyết định). Claim đi đúng đường `ClaimRequest` (300 ký tự, Guardrail). Audio không lưu, không log; log chỉ ghi độ dài, trạng thái, ms |
| Lỗi phía client | Từ chối quyền micro → toast hướng dẫn; không có micro → ẩn nút; `429` / `503` → toast, giữ nguyên chữ đã gõ |

---

## 12. Chiến lược test trong lúc xây

### 12.1. Các tầng

| Tầng | Công cụ | Thư mục | Ước lượng |
|---|---|---|---|
| Lõi tất định (số, chuẩn hoá, prosody, SSML, kịch bản đọc, guard) | pytest + hypothesis | `backend/tests/speech/` | ~110 |
| Dịch vụ (mock, Azure qua `httpx.MockTransport`, gate, breaker, cache, kho bền, Narrator) | pytest (+ marker `azurite`) | `backend/tests/speech/` | ~60 |
| API / WS / CLI | pytest + TestClient | `backend/tests/api/`, `tests/cli/` | ~40 |
| Contract | pytest + jsonschema; Vitest + ajv | `tests/contract/` (cả hai phía) | ~10 |
| Frontend unit / property | Vitest + fast-check | `frontend/tests/unit/` | ~45 |
| Component | Vitest + Testing Library | `frontend/tests/component/` | ~15 |
| E2E | Playwright (backend thật, mock TTS) + project `mock` | `frontend/tests/e2e/`, `e2e-mock/` | ~12 |
| Live (opt-in) | `pytest -m live`, cần `SPEECH_KEY` | `backend/tests/live/` | ~8 |

### 12.2. Ca bắt buộc theo module

| Module | Ca |
|---|---|
| `spoken_numbers` | Bảng vàng: 0–30, các mốc 40 / 41 / 44 / 45 / 50 / 99 / 100 / 101 / 105 / 110 / 115 / 121 / 1 000 / 1 005 / 10 400 / 999 999; thập phân `0.05`, `0.35`, `2.08`, `2.10`, `10.4`; thứ tự 1 / 2 / 4; số âm. Property: `parse_vi_number(vi_cardinal(n)) == n` với mọi n ≤ 999 999; thập phân 0–100 với 1–2 chữ số lẻ |
| `spoken` | Mọi dòng bảng §5.3–5.5; ví dụ §5.6; tỷ số kẹp tên đội cho cả 4 đội; "phút 63" không thành "phút phút"; "không được công nhận" không bị đổi; không chữ số nào sót lại trong VI. **Quét toàn bộ:** mọi câu `speak` (cả hai giọng), mọi câu mở đầu theo CLB, mọi bản ngắn của luật, mọi recap và lời chào của 4 kịch bản × 3 seed qua chuẩn hoá + SpeechGuard: 0 trượt |
| `prosody` / `ssml` | Bảng preset (test vàng); chọn preset cho mọi kind; burst chỉ khi đủ điều kiện; SSML vàng cho 5 câu mẫu; mọi thuộc tính nằm trong biên của §6.1; escape `& < > " '`; `clip_id` giống nhau giữa hai tiến trình (không dùng `hash()`) |
| `narration` | Bình luận: thứ tự đoạn, `framing` chỉ ở casual có CLB, giải thích luật luôn là bản ngắn. Recap: chọn khoảnh khắc, bỏ bàn bị VAR huỷ (`comeback_var-7`), có "Phút X:", biên độ dài §4.2, giới hạn 6 / 8 câu, thứ tự theo `seq` |
| `speech_guard` | Từng G1–G7 có ca đúng và ca sai (mutation: đổi một chữ số, bỏ một số, chèn chữ số vào câu đọc, SSML hỏng, sai ngôn ngữ, khoảnh khắc ngoài phạm vi) |
| `mock` | Cùng request ra cùng byte; WAV hợp lệ (đọc bằng `wave`); độ dài theo `MOCK_TTS_TIME_SCALE`; mọi kiểu lỗi; độ trễ |
| `azure_tts` (MockTransport) | Header đúng (key và Entra `aad#…`); body là SSML; 200 + MP3 hợp lệ; `BadAudio` (sai content type, rỗng, không phải MP3); 400 / 401 (Entra làm mới token một lần) / 403 / 415 / 429 (`Retry-After`) / 500 / 502 / 503; timeout; không có key trong `repr` / log |
| `gate` | Token bucket ở biên (18 / phút); ưu tiên; hết hạn chờ → `throttled`; cooldown 429; đồng thời ≤ N; đồng hồ giả |
| `clips` | Ghi nguyên tử (đọc giữa lúc ghi không thấy file dở); LRU theo byte; không xoá clip vừa dùng; dựng lại chỉ mục khi khởi động; dọn file tạm |
| `durable` | `LocalStore` khứ hồi + metadata; `BlobStore` với Azurite (marker `azurite`): put / get / exists, `overwrite=False` idempotent, content type |
| `narrator` | Single-flight (10 lần `cue` cùng clip → 1 lần tổng hợp); cache hit → `exact=True`; breaker mở → `circuit_open`, nửa mở → thử 1 lần; hết ngân sách → `budget`; `speed`; `browser` → `server_off`; SpeechGuard trượt → `guard`, không tổng hợp; thất bại được nhớ, `wait` trả ngay; `aclose` không để lại task |
| `api/audio` | 200, 206, 416, HEAD; header cache; 404; 422 (ID xấu, đuôi lạ, `../`); 503 khi lỗi hoặc quá giờ chờ; clip từ kho bền sau khi khởi động lại |
| `sessions` / WS | Hồ sơ có `voice` nhận `speech` ở mục `speak`; hồ sơ không có `voice` nhận `speech: null` và payload giống hệt Ngày 4 khi bỏ các khoá mới (`speech`, `profile.voice`); `hello` không gây tổng hợp; recap có `speech`; `voice=abc` → `4422`; `TTS_PROVIDER=off` → `speech` luôn `null`; `n` vẫn liên tục |
| CLI | `speak --ssml-only` in SSML hợp lệ; `tts warm` ghi đủ clip vào kho local (mock); `recap --audio` ghi file |
| Frontend | §10.3 (mọi luật + bất biến I1–I8); `AudioPlayer` với phần tử giả (`NotAllowedError` → `needs_gesture`, `error` → `failed`, quá 12 s → `failed`); `BrowserVoice` với `speechSynthesis` giả (chọn giọng, tách câu, `cancel`, không có giọng vi); `profile` khứ hồi có `voice`; `VoiceControl` / `RecapAudio` (mọi trạng thái); i18n đủ khoá |

### 12.3. Property và metamorphic

- **Bất biến số:** với mọi câu sinh từ template (4 × 3 trận) và mọi chuỗi ngẫu nhiên ghép từ Fact display, đội, cầu thủ: số con số đọc = số con số viết, giá trị bằng nhau, đúng thứ tự.
- **Metamorphic:**
  - đổi tên đội trong `MatchContext` → câu đọc chỉ đổi phần tên, số không đổi;
  - thêm khoảng trắng hoặc ngoặc thừa → cùng danh sách số.
- **SSML:** chữ Unicode ngẫu nhiên (hypothesis, gồm `& < > " ' ]]>` và ký tự điều khiển) → SSML luôn parse được và đúng danh sách phần tử cho phép.
- **Bộ lập lịch:** chuỗi input ngẫu nhiên (enqueue với excitement 0–3, ended, failed, pause, resume, mute, tick) → I1–I8 luôn đúng.

### 12.4. Ma trận lỗi TTS

`MOCK_TTS_FAULTS` ∈ {`error`, `timeout`, `throttle`, `bad_audio`, `auth`} × vị trí {câu bình thường, bàn thắng, recap, lời chào} × số lần {1, 3, mãi}. Mỗi ô phải thoả:
- không có `5xx` ngoài `/audio`, không exception lọt ra ngoài;
- message `commentary` vẫn phát đúng giờ (lag studio không tăng);
- breaker đúng trạng thái;
- client nhận `note` đúng hoặc nhận `503` từ `/audio` và chuyển sang trình duyệt;
- không task nào treo sau khi tắt app.

### 12.5. Giữ an toàn cho Ngày 1–4

- `pytest -m "not live"` (1 789 test hiện có) xanh với `TTS_PROVIDER=off` và với `mock`.
- Fixture WS của Ngày 4 ghi lại bằng code mới: payload của 3 hồ sơ cũ giống hệt bản đã commit khi bỏ các khoá mới (`speech`, `profile.voice`). Thêm fixture mới `casual.vi.beginner.club=HAR.voice=female`.
- Vitest, Playwright Ngày 4 (23 test), axe, ngân sách JS (overlay ≤ 160 KB, Studio ≤ 300 KB) giữ nguyên.

### 12.6. CI

- Job backend: thêm `tests/speech/` (mặc định), cập nhật quy tắc import.
- Job frontend: E2E chạy backend với `TTS_PROVIDER=mock MOCK_TTS_TIME_SCALE=0.1 MOCK_TTS_LATENCY_MS=100`; Chromium thêm cờ `--autoplay-policy=no-user-gesture-required`, `--use-fake-ui-for-media-stream`, `--use-fake-device-for-media-stream`.
- `azurite` và `live` không chạy trên CI (không có secret, không cần Node service riêng).

---

## 13. Kế hoạch kiểm thử sau khi hoàn thành các chức năng

Chạy theo thứ tự. Mỗi pha có điều kiện qua; chưa qua thì sửa rồi chạy lại, chưa sang pha sau.

| Pha | Việc | Lệnh / cách làm | Điều kiện qua |
|---|---|---|---|
| **V1. Gate tĩnh** | Backend: ruff, ruff format, mypy strict. Frontend: `npm run check` | `uv run ruff check . ; uv run ruff format --check . ; uv run mypy` · `npm run check` | Sạch |
| **V2. Hồi quy backend** | Toàn bộ test Ngày 1–4 + Ngày 5, kèm coverage | `pytest -m "not live" --cov=pundit`; chạy lại `-m "not live and not slow and not perf"` với `TTS_PROVIDER=off` | 100 % xanh; coverage tổng ≥ 93 %, module `speech` + lõi giọng đọc ≥ 90 % |
| **V3. Lõi tất định** | Quét chuẩn hoá + SpeechGuard trên 4 × 3 trận; property số / SSML; test vàng | `pytest tests/speech -m "not azurite"`; `pytest -m slow -k speech` | 0 trượt guard, 0 chữ số sót, mọi số khứ hồi đúng |
| **V4. Dịch vụ + ma trận lỗi** | Narrator, gate, breaker, kho, Azure qua MockTransport; ma trận §12.4; Azurite | `pytest tests/speech`; `npx azurite-blob --silent --location <tmp>` rồi `pytest -m azurite` | Mọi ô ma trận đạt; Blob khứ hồi; không task treo |
| **V5. API / WS / hợp đồng** | Test API / WS; ghi lại fixture bằng `record_ws.py` (thêm hồ sơ có giọng) rồi so với fixture Ngày 4; contract hai phía | `pytest tests/api tests/contract`; `python scripts/record_ws.py …`; `npm run test` | Fixture cũ giống hệt khi bỏ các khoá mới (`speech`, `profile.voice`); mọi message mới hợp lệ với schema |
| **V6. Frontend unit / component / property** | Scheduler (I1–I8), player, giọng trình duyệt, component | `npm run test -- --coverage` | Xanh; `lib/audio` ≥ 90 % |
| **V7. E2E** | A1–A11 bên dưới, backend thật với mock TTS | `npx playwright test` | 11/11 + E2E Ngày 4 xanh |
| **V8. Hiệu năng** | Replay 1000x khi bật giọng (3 hồ sơ có giọng) so với tắt giọng; event → clip sẵn sàng (mock trễ 300 ms); message → bắt đầu phát ở client (20x); RAM và số file cache sau cả trận | `pytest -m perf -k speech`; `scripts/smoke_voice.py` | Replay chậm thêm ≤ 20 %; event → clip sẵn sàng p95 < 1,5 s; message → bắt đầu phát p95 < 1 s khi hàng đợi rảnh (mock trễ 300 ms); cache ≤ giới hạn; không rò bộ nhớ |
| **V9. Smoke giọng đọc** | `scripts/smoke_voice.py`: server mock (trễ 300 ms), 3 client WS (casual · vi · HAR · beginner · nữ; analyst · vi · nam; casual · en không giọng), `dominant_loser-42` ở 20x. Client tải mọi URL audio như trình duyệt; giữa trận khởi động lại server với `MOCK_TTS_FAULTS=error:5`; recap H1 / FULL | `python scripts/smoke_voice.py` | Số lần tổng hợp = số clip khác nhau; 100 % URL trả 200 hoặc 503 có lý do; chuyển sang trình duyệt đúng lúc lỗi; recap có audio ở H1 và FULL, nằm trong kho bền; hồ sơ EN không có audio; không lỗi trong log; không task treo |
| **V10. Nghe duyệt** (thủ công, 30–45 phút) | `scripts/tts_review.py --scenario dominant_loser --seed 42` (rồi `comeback_var-7`, `red_card_swing-7`) sinh `review/index.html`: mỗi câu có chữ viết, câu đọc, SSML, trình phát audio (mock / Azure) và nút đọc bằng giọng trình duyệt; phần điểm danh 80 cầu thủ; 9 preset với cùng một câu. **Chưa có Azure:** nghe bằng giọng trình duyệt trên Edge. **Có Azure:** nghe bằng giọng thật, cả hai giọng | Mở trang trong Edge | Mọi số đọc đúng; tên đội, cầu thủ, viết tắt nghe được; bàn thắng nghe rõ nhanh và cao hơn; lời giải thích luật chậm hơn. **Mỗi lỗi tìm thấy thành một luật hoặc alias kèm test** |
| **V11. Ghi kết quả** | Mục as-built của tài liệu này; tick `checklist.md`; ghi quyết định vào `claude.md`; commit local | | Tài liệu khớp code |

**E2E (V7)**, backend thật, `TTS_PROVIDER=mock`, `MOCK_TTS_TIME_SCALE=0.1`, trận ở 20x:

| # | Kịch bản | Kiểm tra |
|---|---|---|
| A1 | Fan casual · vi · HAR · beginner, bấm **Bật giọng đọc** (giọng nữ) | Lời chào phát trước; sau đó `window.punditAudio.played` tăng; mỗi `id` đã phát là một `commentary` có `speech`; `maxConcurrent == 1`; thứ tự theo `n` (trừ bàn thắng) |
| A2 | Tới bàn thắng 27' khi đang đọc một câu `excitement` ≤ 1 | Câu đó bị dừng (`preempted` tăng), bàn thắng phát ngay; thẻ bàn thắng có `data-speaking` |
| A3 | Studio Pause (context khác) rồi Resume sau 3 s | Trong lúc dừng không có câu mới bắt đầu; sau Resume đọc tiếp, mục quá hạn bị bỏ |
| A4 | Đổi sang giọng nam | Kết nối mới; URL clip mới khác URL cũ; `GET /audio` trả 200; clip của giọng nam (cue có `voice` NamMinh) |
| A5 | Backend chạy `MOCK_TTS_FAULTS=error`; trang được tiêm `speechSynthesis` giả | Mọi câu chuyển sang `browser` (`fallbacks` tăng); chip "Giọng dự phòng"; không lỗi console |
| A6 | Tới giờ nghỉ: tab Tóm tắt → **Nghe tóm tắt** H1, tua tới giữa, **Nghe lại** | Request `206` khi tua; về 0 khi nghe lại; hàng đợi bình luận dừng trong lúc nghe recap (R10); thời lượng hiển thị khớp `duration_ms` (± 1 s) |
| A7 | Studio, overlay và màn so sánh mở cùng lúc | Không trang nào gọi `/audio` hay `/speech/*`; payload `commentary` của các trang này có `speech: null` |
| A8 | Studio "chạy lại từ phút 45" khi Fan đang nghe | Hàng đợi bị xoá, câu đang đọc dừng; Fan tự kết nối lại và đọc tiếp ở phiên mới |
| A9 | Project mock: mất kết nối 5 s giữa lúc đọc | Không crash; banner kết nối lại; sau `hello` không đọc lại câu cũ |
| A10 | (P2) Studio: micro giả của Chromium, bấm 🎤 rồi dừng | Ô claim có `MOCK_STT_TEXT`; **chưa gửi**; Enter → verdict như Ngày 4 |
| A11 | axe trên Fan khi đang bật giọng và tab Tóm tắt có trình phát | 0 vi phạm serious / critical |

**Nghiệm thu thủ công:**

| # | Việc |
|---|---|
| M1 | Edge, `TTS_PROVIDER=browser`: Fan casual · vi · HAR · beginner, bật giọng nữ, `dominant_loser-42` ở 5x. Nghe lời chào; ít nhất một câu bình thường, một bàn thắng (có "Vào!" nhanh và cao hơn), một lời giải thích luật (chậm hơn) |
| M2 | Đổi sang giọng nam: lời chào và bình luận bằng giọng nam (giọng trình duyệt nếu có NamMinh) |
| M3 | Recap H1 và FULL: nghe, tua, nghe lại; trong lúc nghe recap không có bình luận chen vào |
| M4 | Dừng backend khi đang đọc: giọng dừng, có banner, trang không trắng; bật lại thì đọc tiếp |
| M5 | Pause ở Studio khi Fan đang nghe: câu đang đọc đọc nốt rồi dừng; Resume đọc tiếp |
| M6 | V10 nghe duyệt (trình duyệt trước, Azure khi có key): ghi lỗi vào bảng, sửa kèm test |
| M7 | DevTools mobile (390 × 844) hoặc điện thoại thật: bật giọng bằng chạm; trên iOS có hiện "Chạm để bật âm thanh" khi cần |
| M8 | (P2) Studio: nói "Đội nhà áp đảo hiệp 2" (mock trả đúng câu này; Azure nhận dạng thật) → ô claim được điền → Enter → TRUE |
| M9 | `/healthz`, log server, drawer Agent Trace: không có key, token hay header xác thực |
| M10 | Diễn tập đoạn 20 s "bình luận tiếng Việt có giọng đọc + Người mới xem" của kịch bản video Ngày 7 |

**Tầng B (khi có key Azure):**
1. Spike 30 phút:
   - `GET /voices/list` kiểm tra hai giọng vi-VN, `StyleList`, `WordsPerMinute`;
   - đọc 9 preset × 2 giọng;
   - đo ký tự / giây để hiệu chỉnh `estimate_ms`.
2. `pytest -m live -k speech`:
   - mỗi preset, mỗi giọng trả MP3 hợp lệ, thời lượng lệch ước tính ≤ 30 %;
   - 25 request dồn dập trên F0 không lộ 429 ra ngoài (gate giữ ≤ 18 request / phút);
   - recap dài nhất tổng hợp xong < 15 s.
3. V10 với giọng thật.
4. `smoke_voice.py --provider azure` ở 1x và 20x: event → bắt đầu phát p95 < 5 s.

---

## 14. Quy trình thực hiện (thứ tự + gate)

| Bước | Nội dung | Thời lượng | Gate (phải xanh mới đi tiếp) | Commit |
|---|---|---|---|---|
| G0 | **Spike (≤ 45 phút, không cần Azure):** (a) Edge: `speechSynthesis.getVoices()` có HoaiMy / NamMinh không, rate / pitch có tác dụng không; (b) WAV mock phát được trong Edge và Chromium headless, sự kiện `ended` đúng giờ; (c) `npx azurite-blob` + `azure-storage-blob` put / get; (d) đếm các dạng số của mọi câu `speak` trên 12 trận bằng `extract_numbers` để chốt bảng §5.3 | 0:45 | Ghi kết quả vào as-built; điều chỉnh §5 / §10.4 nếu khác dự kiến | — |
| G1 | Contracts + settings + schema + `gen:types` | 0:45 | Contract test; schema chỉ thêm | `feat(speech): contracts, settings, schemas` |
| G2 | `spoken_numbers` + `spoken` + từ điển theo trận + bộ đọc ngược | 2:00 | Bảng vàng §12.2 + property; quét 4 × 3 trận: 0 chữ số sót | `feat(speech): Vietnamese spoken normalizer` |
| G3 | `prosody` + `ssml` + `narration` + `speech_guard` | 1:30 | Test vàng preset / SSML; guard 0 trượt trên 4 × 3 trận; biên độ dài recap | `feat(speech): narration scripts, SSML presets, SpeechGuard` |
| G4 | `speech/`: ports, mock, Azure (MockTransport), auth, gate, breaker `trip`, `clips`, `durable` (local + Blob) | 2:30 | Ca §12.2; ma trận lỗi; Azurite | `feat(speech): synthesizers, rate gate, clip stores` |
| G5 | `Narrator` + tích hợp session / hub + `api/audio` + recap / greeting / voices + healthz + CLI | 1:45 | Test API / WS; fixture cũ không đổi; `hello` không tổng hợp | `feat(api): narrator, audio endpoint, voice profiles` |
| G6 | Frontend lõi: `scheduler`, `player`, `browser-voice`, `unlock`, `useCommentaryAudio`, `onLive` | 2:00 | Vitest + fast-check I1–I8 | `feat(fan): audio scheduler and players` |
| G7 | Fan UI: `VoiceControl`, thẻ đang đọc, `RecapAudio`, `ProfileSetup`, i18n; ghi lại fixture | 1:30 | Component + E2E A1–A9, A11 | `feat(fan): Vietnamese voice, recap audio, voice choice` |
| G8 | STT (P2): API + mock + Azure + `MicButton` + `recorder` + worklet | 1:30 | Test API STT; E2E A10 | `feat(studio): speak a claim (speech to text)` |
| G9 | Kế hoạch V1–V11, sửa lỗi, as-built, checklist, claude.md | 1:30 | Đạt §13 tầng A | `test: Day 5 voice` · `docs: Day 5 voice as-built` |

- **Tổng khoảng 15 giờ** cho phần giọng đọc. Commit local theo từng bước, chưa push (như Ngày 2–4).
- **Nếu trễ, cắt theo thứ tự:**
  1. STT (G8);
  2. gói EN;
  3. `pundit tts warm`;
  4. span Narrator trong drawer (giữ log + `/healthz`);
  5. hiệu ứng thẻ đang đọc (giữ `data-speaking`);
  6. lời chào (thay bằng clip im lặng để mở khoá).
- **Không bao giờ cắt:** G1–G5 (chuẩn hoá số + SpeechGuard + SSML preset + Azure adapter + Narrator + kho bền local / Blob); bộ lập lịch + player + giọng trình duyệt; recap audio + nghe lại; chọn giọng nam / nữ (rẻ); test hồi quy Ngày 1–4.

---

## 15. Bảng chống bug

| # | Bug thường gặp | Phòng tránh (thiết kế) | Phát hiện (test) |
|---|---|---|---|
| 1 | **Đọc sai số** ("2.24" thành "hai nghìn…", "0-1" thành ngày tháng) | Tự đổi mọi số thành chữ (§5); không chữ số nào tới TTS | Bảng vàng, quét 4 × 3 trận, G2, V10 |
| 2 | Đọc số khác số trên màn hình | Tìm số bằng chính `extract_numbers` của Guardrail; khứ hồi bằng bộ đọc ngược độc lập | Property khứ hồi, mutation G2 |
| 3 | "không" (phủ định) bị coi là số 0 | Bộ đọc ngược chỉ đọc trong khoảng của `SpokenNumber` | Ca "không được công nhận", "không thành bàn" |
| 4 | SSML hỏng hoặc bị chèn thẻ (`&`, `<` trong tên) | Escape; thuộc tính chỉ từ bảng; `check_ssml` | Property hypothesis, G5 |
| 5 | Đọc câu chưa kiểm chứng | Kịch bản chỉ nhận nguồn có kiểu (G1); `/audio` không tổng hợp văn bản tuỳ ý | Test kiểu, test API |
| 6 | Hai tiếng chồng nhau | Một phần tử `<audio>`; scheduler thuần; `stop` trước `play` | I1, E2E A1 (`maxConcurrent == 1`) |
| 7 | Hàng đợi phình ở 20x | `MAX_WAITING = 3`; quá hạn theo excitement; `TTS_MAX_SPEED` | I2, I6, A3 |
| 8 | Autoplay bị chặn nên im lặng mà không ai biết | Mở khoá trong cú bấm; bắt `NotAllowedError` → banner | Test player, M7 |
| 9 | Giọng tiếp tục khi trận đã dừng hoặc đã restart | R9, R11; `status.restarted` → `clear` | A3, A8 |
| 10 | Đọc lại câu cũ sau khi kết nối lại | `hello` không có `speech`; nhớ 100 `id` đã thấy | A9, test `hello` |
| 11 | Tổng hợp trùng (nhiều người nghe cùng câu) | `clip_id` theo nội dung + single-flight | Test 10 `cue` → 1 lần tổng hợp |
| 12 | Tốn quota vì `hello` | V5: `hello` không tổng hợp | Test 0 lần tổng hợp |
| 13 | 429 dồn dập trên F0 | `RateGate` 18 / phút + ưu tiên + `Retry-After` + breaker | Test gate, live 25 request |
| 14 | Token Entra hết hạn giữa trận | Cache, làm mới trước 5 phút; 401 → làm mới một lần | Test auth (MockTransport) |
| 15 | Lộ key / token | `SecretStr`; không log header; `describe()` không có bí mật | Test `repr` / log / healthz, M9 |
| 16 | File clip đọc dở | Ghi file tạm + `os.replace` | Test đọc trong lúc ghi |
| 17 | Cache đầy đĩa | LRU theo byte, không xoá clip mới dùng | Test LRU |
| 18 | Clip bị xoá đúng lúc client sắp tải | Giữ clip dùng trong 10 phút; kho bền cho clip Azure | Test LRU, A6 |
| 19 | Phục vụ WAV của mock như MP3 sau khi đổi provider | `provider` nằm trong `clip_id`; đuôi file theo `media_type` | Test `clip_id` |
| 20 | Path traversal qua `/audio` | Regex `^[0-9a-f]{32}$`; đuôi trong danh sách cho phép | Test `422` |
| 21 | Gãy client hoặc fixture Ngày 4 | Chỉ thêm trường; `key()` không đổi khi không có `voice`; `TTS_PROVIDER=off` | V5 so fixture, hồi quy |
| 22 | Recap đọc bàn thắng đã bị VAR huỷ | Bỏ moment bàn thắng bị huỷ (§4.2) | Test `comeback_var-7` |
| 23 | Recap nêu tỷ số cũ như tỷ số cuối | "Phút X:" trước mỗi câu khoảnh khắc | Test narration |
| 24 | Tên nước ngoài đọc khó nghe | Đọc họ, bỏ chữ viết tắt; bảng `PRONUNCIATION_VI` sau nghe duyệt | V10 điểm danh 80 tên |
| 25 | Giọng trình duyệt cắt câu dài (Chrome) | Tách utterance theo câu ≤ 200 ký tự | Test browser-voice |
| 26 | Safari / iOS chưa có danh sách giọng | Chờ `voiceschanged` ≤ 1 s; mở khoá `speechSynthesis` trong cú bấm | Test giả lập, M7 |
| 27 | Recap và bình luận cùng phát | R10 | A6 |
| 28 | Task tổng hợp treo khi tắt app | `Narrator.aclose` huỷ và chờ; mở rộng test chống rò rỉ task | Test shutdown |
| 29 | Breaker của giọng làm hỏng claim / studio | Breaker riêng | Test độc lập breaker |
| 30 | Thời lượng sai làm UI lệch | `exact` cho biết ước tính hay đo thật; trình phát dùng thời lượng thật khi đã tải | Test MP3 / WAV, A6 |
| 31 | Chi phí vượt dự kiến | Ngân sách ký tự mỗi ngày; cache; budget alert Azure (§17) | Test ngân sách, `/healthz` |
| 32 | STT tự gửi claim sai | Chỉ điền ô, người dùng tự gửi | A10 |
| 33 | Lưu hoặc log audio của người dùng | Không lưu, không log nội dung | Review code, test log |
| 34 | Windows chặn binary mới (ví dụ `cryptography` của `azure-storage-blob`) | Import trễ; test Blob là opt-in; chạy bằng Python gốc của uv như Ngày 3–4 | G0 (c) |

---

## 16. Rủi ro và phương án

| Rủi ro | Khả năng | Phương án |
|---|---|---|
| Không có Azure tới lúc nộp bài | Trung bình | Tầng A đủ để demo: `TTS_PROVIDER=browser` trên bản public (giọng trình duyệt, ghi rõ là dự phòng), hoặc quay video trên Edge. README nêu rõ đường Azure đã có test (MockTransport) nhưng chưa chạy trên Azure thật |
| Edge không có giọng Việt "Online (Natural)" qua Web Speech API | Thấp – trung bình | G0 kiểm tra. Nếu không có: dùng giọng Việt có sẵn của Windows nếu có; không có thì nghe duyệt chữ đọc trên trang review và dời nghe giọng tới tầng B |
| Đường dẫn TTS trên custom domain cho Entra khác dự kiến | Trung bình | `SPEECH_ENDPOINT` là URL gốc, đường dẫn là hằng số có test; kiểm tra một request ở tầng C; dự phòng: key trong Key Vault qua secret reference của Container Apps (vẫn không có key trong code) |
| F0 chỉ 20 request / 60 s, nhiều giám khảo cùng nghe | Trung bình | Gate + giọng trình duyệt khi `throttled`; `tts warm` cho kịch bản demo; cân nhắc S0 trong thời gian chấm (khoảng 0,1 USD mỗi trận × hồ sơ, theo giá 15 USD / 1 triệu ký tự) |
| 429 do năng lực của giọng ở region | Thấp – trung bình | Đặt Speech ở region gần (Southeast Asia) hoặc cùng region với Container App; xử lý 429 như trên |
| Giọng vi-VN đọc chưa tự nhiên với cách viết số đã chọn ("ba mươi lăm" hay "ba lăm") | Trung bình | Bảng đọc là dữ liệu có test; V10 chốt; đổi bảng không đổi kiến trúc |
| Tên cầu thủ nước ngoài khó nghe | Cao | Đọc họ; bảng alias sau V10; tên Việt đọc tự nhiên |
| Recap casual dưới 1 phút | Chắc chắn (có chủ đích) | Ghi rõ ở §4.2 và §18; analyst đạt 1–1,5 phút |
| Autoplay / iOS | Trung bình | Mở khoá trong cú bấm, một phần tử audio, banner "Chạm để bật âm thanh" |
| 15 giờ cho phần giọng, chưa tính phần triển khai | Cao | Gate theo §14, cắt theo thứ tự; lõi tất định làm trước |
| Windows Application Control chặn gói native mới | Thấp | Như Ngày 3–4: Python gốc của uv + site-packages; Azurite chạy bằng Node |
| Giám khảo bật giọng ở bản public khi đang 1000x | Thấp | `TTS_MAX_SPEED` → không đọc; UI ghi "Giọng đọc tắt khi tua nhanh" |

---

## 17. Hợp đồng cho phần triển khai (Ngày 5) và Ngày 6+

| Hợp đồng | Bên dùng | Quy tắc |
|---|---|---|
| Biến môi trường §3.7 | Container App | Trên Azure: `TTS_PROVIDER=azure`, `SPEECH_ENDPOINT`, `SPEECH_RESOURCE_ID`, `AUDIO_STORE=blob`, `AUDIO_BLOB_ACCOUNT_URL`, `AUDIO_CACHE_DIR=/tmp/pundit-audio`. **Không** đặt `SPEECH_KEY` |
| Tài nguyên Speech | Bicep | Speech riêng hoặc dùng chung tài nguyên Foundry (AIServices); **custom subdomain** (`customSubDomainName`, không đổi lại được); cùng region với Container App, hoặc Southeast Asia nếu tách |
| Vai trò của Managed Identity | Bicep | **Cognitive Services Speech User** trên tài nguyên Speech; **Storage Blob Data Contributor** trên storage account (hoặc riêng container `audio`) |
| Storage | Bicep | StorageV2, Standard_LRS, `allowBlobPublicAccess: false`, container `audio` private |
| Image | Dockerfile | Cài `azure-identity`, `azure-storage-blob`, `httpx` (theo `uv.lock`); không cần gói audio hệ thống vì dùng REST |
| `/healthz.speech` | Smoke test sau deploy | `provider == "azure"`, `breaker == "closed"`, `store == "blob"` |
| Log `speech.synthesize` / `speech.recognize` | App Insights | Trường cố định §8.9; không có nội dung câu, không có bí mật |
| Budget | Azure Cost Management | Alert 50 % / 80 % như claude.md §6.0; `TTS_DAILY_CHAR_BUDGET` là lưới thứ hai |
| `pundit tts warm` | Trước giờ chấm | Làm ấm clip của kịch bản demo vào Blob |
| Ngày 6 | Đo độ trễ trên bản deploy | Event → âm thanh p95 < 5 s (claude.md §6.2); đo tỷ lệ clip `throttled` / `circuit_open` |
| `SpeechCue`, `AudioRef`, `/audio`, `/speech/*` | Ngày 6+ | Chỉ thêm trường; đổi đường dẫn thì phải giữ đường cũ |

---

## 18. Quyết định đã chốt (22/10/2026)

| # | Quyết định | Chốt |
|---|---|---|
| 1 | Cách đưa audio tới Fan | ✅ **Luồng audio theo câu:** mỗi câu một clip HTTP định danh theo nội dung (bất biến, cache vĩnh viễn); message WS mang tham chiếu; không gửi byte audio qua WS, không stream clip đang tổng hợp dở |
| 2 | Khi nào tổng hợp | ✅ Ngay lúc phát message live, chỉ cho hồ sơ có `voice`; `hello` / `resync` / REST bình luận không bao giờ tổng hợp |
| 3 | Chưa có Azure | ✅ **Mock-first:** `TTS_PROVIDER = off \| browser \| mock \| azure`, mặc định `mock` (CI, E2E); dùng `browser` để nghe tiếng Việt trước khi có Azure; Azure gắn bằng cấu hình |
| 4 | Dự phòng | ✅ **Giọng trình duyệt** (Web Speech API) đọc đúng các đoạn và prosody của server; có nhãn; không có giọng Việt thì chỉ hiện chữ |
| 5 | Ngôn ngữ | ✅ **VI trước**; EN bật bằng `TTS_LANGUAGES` (P2) |
| 6 | Đọc số | ✅ Tự chuyển mọi số thành chữ bằng code tất định, tìm số bằng `extract_numbers` của Guardrail, SpeechGuard kiểm tra khứ hồi; không dùng `say-as` |
| 7 | Nhấn giọng | ✅ Chỉ `prosody` (rate / pitch / volume) + `break` + tách câu cảm thán đầu (burst). Không dùng `emphasis` (chỉ 3 giọng en-US có), style hay `<lang>` |
| 8 | Recap audio | ✅ Mở đầu (công bố giọng AI + dữ liệu tổng hợp) + đoạn recap đã kiểm chứng + câu bình luận của các khoảnh khắc then chốt (có "Phút X:") + kết. Analyst khoảng 1–1,5 phút; casual khoảng 40–60 s (ngắn có chủ đích) |
| 9 | Lưu trữ | ✅ Cache cục bộ LRU cho mọi clip; kho bền (local ngay bây giờ, Blob trên Azure) cho clip Azure, recap và lời chào; phục vụ qua API, không SAS, không public blob |
| 10 | Gọi Azure | ✅ REST qua `httpx`, không dùng Speech SDK; key khi dev; Managed Identity (`aad#resourceId#token`, custom subdomain, vai trò Speech User) trên Azure |
| 11 | Chọn giọng | ✅ `voice=female\|male` trong hồ sơ WS; mặc định tắt khi tải trang; nhớ lựa chọn giọng; bật cần một cú chạm |
| 12 | Người mới xem trong audio | ✅ Luôn đọc bản ngắn của lời giải thích luật (thẻ vẫn hiện bản đầy đủ) |
| 13 | Narrator | ✅ Agent tất định chạy sau Personalization, ngoài workflow MAF; breaker, cổng giới hạn và trace riêng |
| 14 | STT | ✅ **P2, thiết kế đủ, làm cuối, cắt đầu tiên:** WAV 16 kHz → REST short audio; chữ chỉ điền vào ô claim, không tự gửi |
| 15 | Lời chào | ✅ Phát khi bật giọng: công bố giọng AI và dữ liệu tổng hợp, thử loa, mở khoá autoplay |

---

## 19. As-built (23/10/2026)

### 19.1. Đã xây

- **Backend G1–G5 (giọng đọc):**
  - Hợp đồng chỉ thêm khoá: `Profile.voice`, `CommentaryView.speech`, `RecapView.speech`, `SpeechCue` / `SpeechSegment` / `AudioRef` / `SpeechVoice` / `RecognitionResult`; 4 schema mới trong `packages/schemas`, type frontend sinh lại.
  - Chuẩn hoá đọc tiếng Việt (`spoken_numbers`, `spoken`, từ điển theo trận) có bộ đọc ngược làm oracle; prosody 9 preset + SSML (chỉ `prosody` + `break`); kịch bản đọc (`narration`); SpeechGuard G1–G7.
  - `speech/`: port, mock WAV tất định, Azure TTS qua REST (key hoặc Entra `aad#resourceId#token`), `RateGate`, breaker có `trip`, ngân sách ký tự, cache clip LRU (ghi nguyên tử), kho bền `LocalStore` / `BlobStore`, `Narrator` (single-flight, span trong Agent Trace), làm ấm clip (`warm.py`).
  - API: `GET|HEAD /audio/{clip}` (Range, `immutable`, 503 có lý do), `/speech/voices`, `/speech/greeting`, recap `?voice=`, khối `speech` trong `/healthz`; phiên chỉ gắn `speech` cho message live của hồ sơ có `voice`.
  - CLI: `pundit speak`, `pundit tts warm`, `pundit recap --audio`. `.env.example` chỉ có tên biến.
- **Frontend G6–G7:** `lib/audio/` (`scheduler` thuần, `player`, `browser-voice`, `controller`), `useCommentaryAudio`, `onLive` trong `useMatchConnection`, `useSpeechHealth`; `VoiceControl` (bật / tắt, nữ / nam, chip nguồn giọng, bỏ qua, phím V / N), thẻ đang đọc, `RecapAudio` (nghe, tua, nghe lại, giọng trình duyệt), chọn giọng trong `ProfileSetup`, khoá i18n `fan.voice*` / `fan.recap*`.
- **G8 STT (P2):** `Transcriber` (kiểm WAV, một lần gọi một lúc, 10 lần mỗi phút, bản ghi dưới 300 ms không gọi), `azure_stt.py` (REST short audio), `POST /speech/recognize` (chỉ mount khi `STT_PROVIDER` khác `off`); Studio có `MicButton` (AudioWorklet `public/worklets/pcm-capture.js`, `recorder.ts` tải động), chữ chỉ điền vào ô claim.
- **Công cụ:** `scripts/smoke_voice.py` (V9), `scripts/tts_review.py` (V10), `scripts/record_ws.py` ghi thêm fixture có giọng `dominant_loser-42.casual-vi-beginner-har-female`.
- **Commit (local, chưa push):** `docs: Day 5 Vietnamese voice design` · `feat(speech): Vietnamese voice backend (Day 5 G1-G5)` · `feat(fan): Vietnamese voice, recap audio, voice choice (Day 5 G6-G7)` · `feat(studio): speak a claim (speech to text, Day 5 G8)` · `fix(ui): no prefetch on the landing page's match links` · `test: Day 5 voice (smoke V9, perf V8, listening review V10)` · `fix(speech): a Narrator span never starts before the studio job` · `docs: Day 5 voice as-built`.

### 19.2. Khác biệt so với thiết kế và lý do

| # | Chỗ | Thiết kế | As-built | Lý do |
|---|---|---|---|---|
| 1 | Số đoạn tối đa của một cue | 16 | `MAX_SPEECH_SEGMENTS = 24` | Recap có mở đầu, đoạn recap, các khoảnh khắc ("Phút X: …") và kết, vượt 16 đoạn |
| 2 | Độ dài recap | Analyst 1–1,5 phút, casual 40–60 s | Trần 120 s; quá thì bỏ dần khoảnh khắc phụ, mới nhất trước (`fit_recap`). Ước tính cho `dominant_loser-42` casual: H1 51 s, FULL 65 s | Ước tính trên 12 trận trước khi cắt: H1 49–95 s, FULL 64–147 s. Bỏ khoảnh khắc phụ giữ được bàn thắng, thẻ đỏ, VAR |
| 3 | Giải thích luật trong audio | Tiêu đề + bản ngắn | Chỉ đọc bản ngắn | Bản ngắn đã mở đầu bằng tên luật; đọc thêm tiêu đề thành lặp |
| 4 | `TTS_MAX_RPM` | 18 / phút | Không đặt thì theo provider: azure 18, mock 60 000 | Cổng an toàn cho F0 làm mock chậm vô ích (`tts warm` mất 28 s, nay 3,1 s cho 210 clip) |
| 5 | Đường dẫn worklet | `public/audio/pcm-worklet.js` | `public/worklets/pcm-capture.js` | Route `/audio/{clip}` của backend chặn mọi đường `/audio/*` (trả 422) |
| 6 | STT khi `STT_PROVIDER=off` | `503` | Route không được mount (`404`); `/healthz` báo `stt: off`, nút micro ẩn | Giữ app y như trước khi tắt STT, cùng nguyên tắc với `TTS_PROVIDER=off` |
| 7 | Bản ghi rất ngắn | — | Dưới 300 ms trả `silence`, không gọi recognizer, không tính vào hạn mức | Một cú bấm nhầm không tốn quota F0 |
| 8 | Trạng thái micro | Bấm là "đang ghi" | Thêm pha "đang mở micro": chỉ hiện "đang ghi" khi micro đã có mẫu âm thanh (chờ tối đa 4 s) | Đo trên Edge: lần dùng đầu, mẫu đầu tiên tới sau 1,4 s; người nói ngay sẽ mất lời đầu (lỗi do A10 phát hiện, mục 19.4) |
| 9 | `reset` khi có hello mới | Bỏ mọi mục đang chờ | Giữ lời chào (`greeting`) | Đổi giọng khi đang đọc thì lời chào giọng mới chờ phía sau; hello của kết nối mới không được xoá nó |
| 10 | A2 (bàn thắng chen ngang) | Backend thật | Project `mock`: tự gửi câu "ít kịch tính" (clip 6 s) rồi bàn thắng sau 1,3 s | Cần thời điểm chính xác; trên backend thật, clip 0,1× quá ngắn nên không bao giờ chen. Luật R2 còn có unit + property test |
| 11 | A3 (Pause / Resume) | Kiểm hàng đợi lúc dừng | Kiểm tích hợp: `paused` trong `window.punditAudio`, 3 s không có câu mới, Resume đọc tiếp | Trên backend thật không dựng được hàng đợi đầy đúng lúc; luật R9 có unit + property test |
| 12 | A5 (lỗi TTS) | Backend chạy `MOCK_TTS_FAULTS=error` | `page.route` trả 503 cho mọi clip, cùng đường phía client | Backend E2E dùng chung cho mọi test; lỗi phía server được kiểm ở smoke V9 và test backend |
| 13 | V9 (smoke) | Khởi động lại server giữa trận với `error:5` | Lượt thứ hai là một trận mới với `MOCK_TTS_FAULTS=error:6`, breaker cooldown 10 s | Phiên nằm trong bộ nhớ, khởi động lại là mất trận. Với ít lỗi hơn số lời gọi đang chạy (vd. `error:3`), một lời gọi thành công đóng breaker ngay, nên không thấy `circuit_open` |
| 14 | Cờ `--autoplay-policy=no-user-gesture-required` | Có trong E2E | Không dùng | Test qua với chính sách autoplay thật (cú click của Playwright là cử chỉ người dùng), gần trình duyệt thật hơn. Có cờ micro giả cho A10 |
| 15 | Fixture Ngày 4 ghi lại | Giống hệt khi bỏ khoá mới | Giống hệt, trừ `match.studio.lag_ms_p95` trong hello giữa trận (321,1 → 299,1 ms) | Đó là số đo thời gian thật, đổi sau mỗi lần ghi, không phải do Ngày 5 |
| 16 | G0: giọng tiếng Việt của Edge | Có HoaiMy / NamMinh Online | Edge dưới Playwright chỉ có 3 giọng SAPI en-US; máy này không có giọng vi | E2E tiêm `speechSynthesis` giả (`tests/voice.ts`). Nghe bằng tai (M1, M2, V10) cần Edge có mạng với giọng Online, hoặc Azure |
| 17 | Test live Azure (`pytest -m live -k speech`) | ~8 test | Chưa viết | Chưa có key: viết và chạy ở tầng B, khi có Azure |

### 19.3. Kết quả kiểm thử theo §13

| Pha | Kết quả |
|---|---|
| **V1. Gate tĩnh** | Backend: `ruff check .`, `ruff format --check .` (223 file), `mypy` (126 file): **sạch**. Frontend: `npm run check` (type sinh từ schema không lệch, eslint, typecheck, prettier): **sạch** |
| **V2. Hồi quy backend** | Lần 1, `pytest -m "not live" --cov=pundit` (gồm slow; test perf tự bỏ qua khi đo coverage): 2024 passed, 12 skipped, **1 failed**: `test_voices_greeting_health_and_trace` hỏng ngẫu nhiên do làm tròn (mục 19.4 dòng 12). Sau khi sửa: `pytest -m "not live"` (gồm slow, perf cả của Ngày 5, Blob với Azurite): **2040 passed**, 1 skipped (test chỉ chạy khi chưa cài azure-identity), 0 failed, trong 8 phút 25 giây. `TTS_PROVIDER=off`, `-m "not live and not slow and not perf"`: **1973 passed**, 1 skipped. Coverage tổng **95 %** (ngưỡng 93 %); module giọng đọc (`speech/*`, `spoken*`, `ssml`, `prosody`, `narration`, `speech_cue`, `speech_guard`, `voices`, `api/audio`) khoảng **95 %** số dòng; thấp nhất: narrator 89 %, speech_guard 87 % (ngưỡng 90 % cho cả nhóm) |
| **V3. Lõi tất định** | Quét chuẩn hoá + SpeechGuard trên 4 kịch bản × 3 seed (mọi câu `speak` của cả hai giọng, câu mở đầu theo CLB, bản ngắn của luật, recap, lời chào): **0 trượt guard, 0 chữ số sót, mọi số khứ hồi đúng**. Property: `parse_vi_number(vi_cardinal(n)) == n` với mọi n ≤ 999 999 999; SSML luôn parse được với chữ Unicode ngẫu nhiên |
| **V4. Dịch vụ + ma trận lỗi** | Narrator, gate, breaker, cache, kho bền, Azure qua `httpx.MockTransport` (mọi mã 400 / 401 làm mới token / 403 / 415 / 429 + `Retry-After` / 5xx, timeout, `BadAudio`): xanh. `BlobStore` chạy thật với **Azurite** (put / get / exists, `overwrite=False`, content type) |
| **V5. API / WS / hợp đồng** | Test API / WS / CLI xanh. Fixture Ngày 4 ghi lại: giống hệt khi bỏ `speech` và `profile.voice` (trừ mục 19.2 dòng 15). Fixture mới có giọng: `n` liên tục, 25 câu `speak` đều có clip, `hello` không đọc, recap H1 / FULL có audio; hồ sơ không có giọng không bao giờ nhận `speech`. Mọi message hợp lệ với schema |
| **V6. Frontend** | Vitest **146 passed** (14 file). Dòng phủ `lib/audio`: **98,5 %** (scheduler 100 %, controller 98,7 %, recorder 100 %, browser-voice 97,3 %, player 95,5 %). Property I1–I8 (400 chuỗi ngẫu nhiên mỗi lần chạy) bắt được cả 4 lỗi cài thử: hai giọng cùng lúc, chen ngang lúc dừng, không bỏ câu quá hạn, bỏ bàn thắng khi đầy hàng đợi |
| **V7. E2E** | Playwright **35/35**: Ngày 4 (23 + E1b mới) và Ngày 5 A1–A11 (A1, A3–A8, A10, A11 trên backend thật với mock TTS 0,1×; A2, A9 ở project `mock`). Không lỗi console, `maxConcurrent` luôn 1 |
| **V8. Hiệu năng** | Replay 1000x có 3 hồ sơ nghe (mọi clip đều tổng hợp): **7,90 s so với 7,89 s** khi tắt giọng (+0,1 %, ngưỡng 20 %). Câu → clip sẵn sàng ở 200x, mock 300 ms: **p95 328 ms** (min 285, max 585) trên 62 clip, nên event → clip < 300 ms (lag studio Ngày 3) + 328 ms, dưới ngưỡng 1,5 s. Cache: số file đúng bằng số clip đã làm |
| **V9. Smoke giọng đọc** | `smoke_voice.py --speed 60`: **PASSED** cả hai lượt. Sạch: 50 clip đều 200, mỗi clip tổng hợp một lần, message → clip p95 453 ms, recap H1 / FULL × 2 giọng có trong kho bền, hồ sơ EN không có audio, đúng giọng theo hồ sơ. Có lỗi (`error:6`): 6 × 503 `unavailable`, breaker mở (câu mang note `circuit_open` cho giọng trình duyệt) rồi đóng, các clip còn lại 200, recap vẫn có audio. Không lỗi trong log, không task treo |
| **V10. Nghe duyệt** | `tts_review.py` sinh `backend/review/index.html` (`dominant_loser-42`, giọng nữ): 106 dòng, 64 clip, **0 câu bị SpeechGuard từ chối**, không chữ số nào trong câu đọc. **Phần nghe bằng tai chưa làm:** máy này không có giọng tiếng Việt (mục 19.2 dòng 16) |
| **V11. Ghi kết quả** | Mục này; `checklist.md`; `claude.md`; commit local (chưa push) |

Nghiệm thu thủ công M1–M10 (§13) chưa làm vì cần nghe được tiếng Việt: Edge có giọng Online (cần mạng) với `TTS_PROVIDER=browser`, hoặc key Azure. Các hành vi kiểm được bằng máy của M3–M5, M7–M9 đã có E2E tương ứng (A6, A3, A8, A9, A10, A11).

### 19.4. Lỗi tìm thấy khi kiểm thử và cách sửa

| # | Lỗi | Tìm bằng | Sửa |
|---|---|---|---|
| 1 | Span của Narrator trong Agent Trace bắt đầu ở 0 ms: director lưu trace sau khi đã publish | Test API trace | Lưu trace ngay khi có output, rồi mới publish |
| 2 | Mock bị cổng F0 (18 / phút) làm chậm | `pundit tts warm` mất 28 s | Hạn mức mặc định theo provider (mục 19.2 dòng 4) |
| 3 | Chuẩn hoá: lệch chỉ số cặp số sau khi lọc, "âm" nằm ngoài khoảng số, viết hoa đầu câu, ranh giới từ của "phút" / số thứ tự | Bảng vàng + property | Duyệt danh sách gốc, gộp "âm" vào phần đọc của số, luật viết hoa riêng |
| 4 | Hook đọc `ref` trong render; `setState` trong effect để reset recap | eslint (React Compiler) | Controller giữ bằng `useState`; recap reset bằng `key` theo phạm vi / ngôn ngữ / chế độ / giọng |
| 5 | `destroy()` xoá listener: StrictMode chạy cleanup một lần khi mount thì controller chết | Đọc lại cleanup | `release()`: tắt tiếng, dừng ticker, controller vẫn dùng được |
| 6 | Kết nối lại xoá lời chào đang chờ | Viết test R11 | `reset` giữ mục `greeting` |
| 7 | Micro báo "đang ghi" trước khi có tiếng: lần dùng đầu mất 1,4 s lời nói | E2E A10 (bản ghi chỉ 192 ms cho 1,5 s nói) | Pha "đang mở micro"; `Recorder.start` chỉ xong khi có mẫu đầu tiên |
| 8 | Bấm Esc hoặc rời trang khi micro đang mở: micro mở xong vẫn chạy | Component test | Mỗi lần mở có số thứ tự; mở xong mà đã bị huỷ thì đóng ngay |
| 9 | Trang chọn trận log 2 lỗi 404 cho mỗi trận đang có (prefetch của Next 16 static export tìm `__next.studio.__PAGE__.txt`, file nằm ở `__next.studio/__PAGE__.txt`). Lỗi có từ Ngày 4, chỉ lộ khi server đã có trận | E1 trên server dùng lại | `prefetch={false}` cho link của trận; thêm E1b tạo trận trước |
| 10 | `play()` trả `undefined` (trình duyệt cũ, jsdom) làm `.catch` ném lỗi; "Nghe lại" bỏ lửng promise bị từ chối | Test hook | Bọc `play()` thành promise; bắt lỗi khi nghe lại |
| 11 | Test ClaimBox giả định fetch đầu tiên là `/claims/verify` (nay nút micro hỏi `/healthz` trước) | Vitest | Tìm lời gọi theo URL |
| 12 | Span của Narrator có thể bắt đầu trước lúc job studio kết thúc 0,2 µs: `start_ms` làm tròn xuống ở 3 chữ số (45,2902 thành 45,290), nên test trace hỏng hay qua tuỳ số đo | Hồi quy V2 | Làm tròn lên; thêm test với 4 giá trị biên |

### 19.5. Cách chạy

```bash
cd frontend && npm ci && npm run build
cd ../backend
uv run pundit serve --ui ../frontend/out                         # giọng mock (mặc định)
TTS_PROVIDER=browser uv run pundit serve --ui ../frontend/out    # nghe tiếng Việt bằng giọng của Edge
STT_PROVIDER=mock uv run pundit serve --ui ../frontend/out       # nút micro trong Studio (chữ mẫu)
uv run pundit speak --text "Phút 27, M. Ribas ghi bàn" --scenario dominant_loser --ssml-only
uv run pundit tts warm --scenario dominant_loser --seed 42       # làm ấm clip vào kho bền
uv run pundit recap --scenario dominant_loser --seed 42 --scope FULL --audio recap.wav
uv run python scripts/smoke_voice.py --speed 60                  # V9
uv run python scripts/tts_review.py --scenario dominant_loser --seed 42   # V10 -> review/index.html
npx azurite-blob --silent --location <thư mục>                   # rồi: uv run pytest tests/speech -k azurite
cd ../frontend && npx playwright test                            # e2e + mock (A1–A11)
```

- Backend cho E2E cần `TTS_PROVIDER=mock MOCK_TTS_TIME_SCALE=0.1 MOCK_TTS_LATENCY_MS=100 STT_PROVIDER=mock` (`webServer.env` trong `playwright.config.ts` đặt sẵn; tự bật server thì đặt các biến này).
- Lên Azure: `TTS_PROVIDER=azure`, `SPEECH_ENDPOINT` + `SPEECH_RESOURCE_ID` (Managed Identity, vai trò Cognitive Services Speech User), `AUDIO_STORE=blob` + `AUDIO_BLOB_ACCOUNT_URL`; `STT_PROVIDER=azure` nếu bật micro.

### 19.6. Việc còn lại

- **Tầng B (khi có key Azure):** spike `/voices/list` (hai giọng vi-VN, `WordsPerMinute`), đọc 9 preset × 2 giọng, hiệu chỉnh `estimate_ms`, viết và chạy test live, smoke `--provider azure`.
- **Nghe bằng tai:** V10 và M1–M10 (Edge có giọng Online, hoặc Azure). Mỗi lỗi nghe ra thành một luật hoặc alias kèm test.
- **Triển khai (phần còn lại của Ngày 5):** Dockerfile một image, Bicep / `azd` (Speech, Storage có container `audio`, Key Vault, App Insights), Managed Identity; `pundit tts warm` trước giờ chấm.
- **Ngày 6:** đo event → âm thanh trên bản deploy (p95 < 5 s), tỷ lệ clip `throttled` / `circuit_open`.
