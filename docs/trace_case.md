# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-014 --seed 11
python -m lab9.trace_case --shape fixed --claim C-014 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case: `seeded_regression` C-014@11

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-014@11`, report `reports/eval_seeded_regression.json` (run ID `E-seeded_regression-20261117-bafe`) |
| Triệu chứng | Grader Lab 8 FAIL `proposal_mismatch`. Ledger ghi **90.000đ** (nhãn 120.000đ) nhưng reply nói "Dạ, LogiX đã hoàn **120.000đ** cho đơn O-515." Fail ở `refund_amount_correct`, `reply_matches_ledger`, `quote_kept`. |
| Bước sai đầu tiên | **#5 `proposal {"amount_vnd": 120000}`** — agent tự trừ 30.000đ đã hoàn trước đó khỏi quote 150.000đ. |
| Vì sao là bước *đầu tiên* | #2 `investigator` đọc đúng đơn O-515 (trễ 5 ngày, hàng hỏng). #3 `quote` đúng: 150.000đ = max(SHIP-LATE-03 30.000, DMG-04 150.000). #4 `refund_history` chỉ là đọc lịch sử (30.000đ), chưa sai. Sai bắt đầu khi #5 đổi số quote; host sau đó trừ thêm 30.000đ lần nữa ⇒ ledger 90.000đ, và #6 `reply` báo số agent tự tính (120.000đ) chứ không phải số host đã ghi. |
| Failure mode | **FM-05** pre_deducted (nguyên nhân) → kéo theo **FM-08** misreport (triệu chứng khách nhìn thấy) |
| Đối chứng | `fixed` C-014@11: #3 `quote 150000` → #4 `reply` "120.000đ"; không có `refund_history`/`proposal`; host tự trừ một lần ⇒ ledger 120.000đ = nhãn = reply. |
| Regression test | `src/tests/test_lab9_trace_case.py`: `test_lab9_trace_case_seeded_regression_c014_is_caught` (assert `reply_matches_ledger`, `refund_amount_correct`, `quote_kept` đều False, ledger 90.000) và `test_lab9_trace_case_fixed_c014_is_clean` (cả ba True, ledger 120.000). Đã thấy fail trên variant lỗi và pass trên `fixed`; chạy trong `pytest -m student -k lab9` của job `eval-gate`. |
| Chưa chắc chắn | Trace không ghi kết quả gọi `issue_refund` nên việc "host trừ lần hai" được suy ra từ ledger (90.000 = 120.000 − 30.000), không đọc trực tiếp từ event. MockLLM cố định nên chưa biết với model thật lỗi xuất hiện ở bao nhiêu % seed. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
