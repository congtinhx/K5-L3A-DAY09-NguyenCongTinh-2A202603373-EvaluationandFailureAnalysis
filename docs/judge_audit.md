# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (backend REPLAY, verdict judge lưu sẵn trong CSV; FAIL = lớp dương).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** | dùng (non-blocking, chỉ cho tone) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** (κ, R) | loại — không dùng làm gate |

Ma trận nhầm lẫn (FAIL dương):
- tone_ok: tp 24 · fp 4 · fn 6 · tn 66. Tỉ lệ FAIL quan sát 0.28 → hiệu chỉnh Rogan-Gladen 0.30.
- helpful: tp 9 · fp 0 · fn 21 · tn 70. Tỉ lệ FAIL quan sát 0.09 → hiệu chỉnh 0.30 (judge báo thấp hơn thực tế hơn 3 lần).

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã với khách khiếu nại. Rubric thêm: bắt buộc xưng "LogiX"/"anh/chị", cấm "em/bạn/nha/nhé". |
| 2 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ bắt từ khoá lịch sự ("Dạ, vui lòng"). Rubric thêm: câu mệnh lệnh/chặn khách liên hệ là FAIL dù có "Dạ". |
| 3 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu câu xin lỗi. Rubric ghi rõ: xin lỗi **không bắt buộc** khi đã giải quyết xong; ngắn gọn đủ ý là PASS. |
| 4 | H-051 "Dạ, LogiX đã refund 90.000đ…" | FAIL | PASS | Người | CS LogiX cho phép từ "refund". Rubric thêm danh sách thuật ngữ tiếng Anh được phép (refund, ship, voucher). |
| 5 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Nhãn người sai (người gán nhãn đọc nhầm ticket). Hành động: sửa nhãn người trong tập calibration, ghi lại lý do; không sửa rubric. |
| 6 | H-094 "Da, LogiX da hoan 40.000đ…" (mất dấu) | PASS | FAIL | Người | Rubric thêm: tiếng Việt mất dấu/dịch máy là FAIL tone. |
| 7 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | Người | Judge không thấy ledger nên không thể biết số tiền sai. Đây là FM-08 → chuyển sang grader code `reply_matches_ledger`, bỏ tiêu chí "đúng số tiền" khỏi rubric judge. |
| 8 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | Không nêu kết quả/số tiền, giọng suồng sã. Rubric helpful thêm: phải nêu trạng thái cụ thể (đã hoàn bao nhiêu / vì sao từ chối / cần gì thêm). |

## Kết luận
- **tone_ok dùng được** (κ 0.757, P 0.857, R 0.8 đều trên sàn) nhưng chỉ cho tiêu chí giọng văn, không chặn release; 7/10 bất đồng là judge dễ dãi với cách xưng hô/mệnh lệnh → cập nhật rubric như bảng trên rồi calibrate lại.
- **helpful bị loại**: recall 0.30 nghĩa là judge bỏ sót 21/30 reply hỏng (fn = 21); κ 0.375 < 0.6. Precision 1.0 không cứu được vì judge gần như luôn nói PASS — một gate dùng judge này sẽ cho qua phần lớn lỗi. Đọc 21 case bỏ sót: 18 case là reply nêu **số tiền khác ledger** (judge không có ledger trong ngữ cảnh, nhiều reply còn kèm văn mẫu xin lỗi dài), 3 case là "Ok bạn, đơn … xử lý rồi nhé" không nêu kết quả. Lỗi tiền phải chấm bằng code (`reply_matches_ledger`), không bằng judge.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (n = 100); cần nhãn người mới (held-out) và có ≥2 người gán nhãn để đo đồng thuận người–người trước khi tin κ.
