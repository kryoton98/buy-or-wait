# LLM audit of the message readings

## Triage

Added by hand on 2026-09-13 for the run below; re-running `--llm-audit` rewrites this file without it. Every
disagreement was replayed with the model's facts in place of that message's rule facts (from
`code/cache/llm_cache.json`, no new model calls) to see whether a forecast input or a decision changes.

| Category | Messages |
|---|---|
| (a) same situation, different label or redundant fact, no forecast effect | 55 |
| (b) the model extracts a fact the rules deliberately ignore | 32 |
| (c) facts differ in a way that changes a forecast input (history reading kept, see below) | 7 |
| Total disagreements | 94 |

| Rule kind → model kind | Messages | Category | What differs, and why |
|---|---|---|---|
| first_salary → first_salary | 27 | (a) | the model adds a `salary_date` equal to the salary date (20), or also marks the first salary `next_only` (7); a new salary series starts on that date either way (sample 15 is one of them) |
| invoice_confirmed → invoice_confirmed | 15 | (b) | the model keeps the other freelance income; the rules stop it (`unconfirmed_income all_variable`) because the message says the other invoices still await approval and only confirmed invoices count — do not invent unsupported income (AGENTS.md §6.3, HANDOVER rule 8) |
| salary_with_arrears → salary_with_arrears | 8 | (b) | the model adds the one-off arrears as a credit (with no date) and marks the regular pay `next_only`; arrears are not added (sample 03, HANDOVER rule 8), and without that credit the forecast is identical |
| salary_resumes → salary_resumes | 8 | (a) | a redundant `salary_date` equal to the resume date (sample 14 is one of them) |
| commission_pending → commission_pending | 6 | (b) | the model sets the salary to the confirmed base; the rules keep the history base and drop only the commission (sample 11: the rule reading matches the reference, the model's changes the decision) |
| internal_transfer → internal_transfer | 6 | (a) | the rules carry an `internal_transfer` fact without event ids, which changes nothing |
| household_income_ended → household_income_ended | 5 | (c) | see below |
| international_salary → regular_salary_confirmed | 4 | (a) | label; the model adds `salary_date` and `next_only`, no forecast effect |
| commission_pending → salary_increase | 3 | (b) | the same pending-commission message labelled as a raise, with the base salary applied (sample 11) |
| salary_increase → salary_increase | 3 | (a) | a redundant `salary_date` |
| household_income_ended → salary_reduced_leave | 2 | (c) | see below |
| international_salary → international_salary | 2 | (a) | `next_only` and/or a redundant `salary_date`, no forecast effect |
| prize_received → prize_scam | 2 | (a) | label only; neither reading has facts (sample 24 is one of them) |
| foreign_bill_pending → unknown | 1 | (a) | label only; no facts either way |
| gig_payout_pending → gig_payout_pending | 1 | (a) | `unconfirmed_income` scope `secondary` instead of `all_variable`; the same series stop |
| receipt_confirmation → regular_salary_confirmed | 1 | (a) | label; `next_only` and `salary_date` on the confirmed salary, no forecast effect |

**(c) Household income ended** (message_30, 37, 42, 119, 180, 187, 203; request_42, 50, 58, 154, 230, 238, 262).
Each message says one household employment record has ended and states a remaining confirmed monthly salary
("The remaining confirmed monthly salary is INR 148000"). The rules stop the secondary income series and keep the
primary salary at its history level; the model reads the stated amount and adds `income_stopped`, which in this
engine stops every income series (six of the seven requests would become not affordable).

A template fix was considered and not applied. The template has the same structure as `commission_pending`: the
stated amount is the pre-split total, and the history component is exactly 62 % of it for all seven users (91,760
of 148,000 INR), as it is exactly 60 % for all nine `commission_pending` users (23,256,000 of 38,760,000 IDR for
sample 11). Sample 11 shows that the reference follows the history component for that structure: the history
reading matches it exactly and the stated amount does not. No solved sample covers `household_income_ended`, so the
history reading is kept. The fix (also emit the stated amount as a permanent `salary_amount`) scored the same on the
25 samples and would have moved three of the 250 rows: request_42 (affordable later → affordable now, safe amount
30,029.17 → 52,100), request_50 and request_58 (affordable with spending changes → affordable now).

**Also found:** the model returns arrears credits without a date, and `forecast.apply_facts` fails on an undated
one-time credit. The decision-time fallback only merges model readings for messages the rules cannot classify (none
today), but `evidence.llm_message_reading` should drop undated one-time credits and debits. Proposed, not applied.

## Audit

Every row of `dataset/messages.csv` was read twice: by the rule-based template reader that the decisions use
(`code/buyorwait/evidence.py`) and by the model below, whose output is validated like the decision-time
fallback. This is a report only: no decision and no row of `output.csv` depends on it. Readings agree when the
template kind and the facts match (type, amount to the cent, currency, date, scope and multiplier; labels and
summaries are ignored).

| Item | Value |
|---|---|
| Provider and model | google `gemini-3.5-flash-lite` |
| Run | 2026-09-13 02:14 UTC |
| Messages | 215 |
| Agree / disagree / no usable model reading | 121 / 94 / 0 |
| Model calls (cache hits) | 215 (0) |
| Input / output tokens | 117,388 / 20,583 |

## Disagreements

### message_08 (user_11): facts

> Berikut informasi penggajian terbaru dari Greenfield Foods. Gaji pokok yang dikonfirmasi adalah IDR 38760000. Komisi dari transaksi yang masih berjalan belum disetujui. Transaksi yang masih berjalan tidak masuk pembayaran sampai komisinya dinyatakan diperoleh. Ref payroll EMP-0008.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `commission_pending`; salary_amount 38,760,000 IDR scope=permanent; unconfirmed_income scope=commission (Confirmed base salary of IDR 38760000 with commission pending approval.)

### message_10 (user_14): facts

> Here’s the latest payroll information from HarborWorks. Regular salary of EUR 2717 resumes on 2025-08-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0010.

- rules: `salary_resumes`; salary_amount 2,717 EUR 2025-08-15 scope=permanent
- model: `salary_resumes`; salary_amount 2,717 EUR 2025-08-15 scope=permanent; salary_date 2025-08-15 (Regular salary of EUR 2717 resumes on 2025-08-15.)

### message_11 (user_15): facts

> A quick update from the payroll team at Riverline Retail. Your first salary will be EUR 1661. The confirmed credit date is 2026-01-15. The money will appear after the bank posts the credit. Payroll ref EMP-0011.

- rules: `first_salary`; salary_amount 1,661 EUR 2026-01-15 scope=permanent
- model: `first_salary`; salary_amount 1,661 EUR 2026-01-15 scope=next_only; salary_date 2026-01-15 (Your first salary of EUR 1661 from Riverline Retail is scheduled to be credited on 2026-01-15.)

### message_13 (user_18): facts

> There’s an update from Summit Bank on your recent account activity. The matching debit and credit came from a transfer between your two accounts. Both accounts are registered under the same account holder. Both entries will remain visible in your transaction history. Txn ref BAN-0013.

- rules: `internal_transfer`; internal_transfer
- model: `internal_transfer`; no facts (An update confirms matching debit and credit entries from an internal transfer between your two accounts.)

### message_17 (user_24): kind

> Here’s the latest account information from PrizeTrack. The prize proceeds have reached your account after withholding. The claim is now closed and there are no further scheduled payments. There won’t be another payment unless a separate prize is confirmed. Account ref FIN-0017.

- rules: `prize_received`; no facts
- model: `prize_scam`; no facts (Notification regarding a prize claim closure with no further scheduled payments.)

### message_18 (user_26): facts

> Halo, ini InvoiceFlow. Klien menyetujui pembayaran faktur sebesar IDR 30780000. Penyelesaian diperkirakan pada 2025-08-15; faktur lain yang diajukan masih menunggu persetujuan. Hanya faktur yang sudah dikonfirmasi yang dapat dimasukkan dalam pembayaran berikutnya. Ref kasus SER-0018.

- rules: `invoice_confirmed`; one_time_credit 30,780,000 IDR 2025-08-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 30,780,000 IDR 2025-08-15 (Klien menyetujui pembayaran faktur sebesar IDR 30780000 yang diperkirakan selesai pada 15 Agustus 2025.)

### message_20 (user_28): facts

> A quick update from the payroll team at Cedar Health. Your regular salary for the next payroll is EUR 1452. The same payroll includes a one-time arrears adjustment of EUR 653.40. Your next payslip will show the regular pay and any one-off adjustment separately. Payroll ref EMP-0020.

- rules: `salary_with_arrears`; salary_amount 1,452 EUR scope=permanent
- model: `salary_with_arrears`; one_time_credit 653.4 EUR; salary_amount 1,452 EUR scope=next_only (Your upcoming payroll includes your regular salary and a one-time arrears adjustment.)

### message_22 (user_32): facts

> BrightPath Media has updated your payroll record. Your first salary will be ZAR 54120. The confirmed credit date is 2025-02-15. The money will appear after the bank posts the credit. Payroll ref EMP-0022.

- rules: `first_salary`; salary_amount 54,120 ZAR 2025-02-15 scope=permanent
- model: `first_salary`; salary_amount 54,120 ZAR 2025-02-15 scope=permanent; salary_date 2025-02-15 (BrightPath Media has confirmed your first salary of ZAR 54120, scheduled to be credited on 2025-02-15.)

### message_23 (user_33): facts

> Cedar Bank has reviewed the transaction on your account. The matching debit and credit came from a transfer between your two accounts. Both entries will remain visible in your transaction history. Txn ref BAN-0023.

- rules: `internal_transfer`; internal_transfer
- model: `internal_transfer`; no facts (Cedar Bank confirmed that matching debit and credit entries resulted from a transfer between your two accounts.)

### message_24 (user_34): facts

> Hi, InvoiceLane here. The client approved an invoice payment of INR 196000. Settlement is expected on 2024-12-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0024.

- rules: `invoice_confirmed`; one_time_credit 196,000 INR 2024-12-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 196,000 INR 2024-12-15 (An invoice payment of INR 196000 has been approved and is expected to settle on 2024-12-15.)

### message_26 (user_36): facts

> Hi, Northstar Labs payroll here. Your monthly salary has increased to USD 2988. The change applies from 2026-07-15. The revised amount will appear on your next payslip. Payroll ref EMP-0026.

- rules: `salary_increase`; salary_amount 2,988 USD 2026-07-15 scope=permanent
- model: `salary_increase`; salary_amount 2,988 USD 2026-07-15 scope=permanent; salary_date 2026-07-15 (Your monthly salary has permanently increased to USD 2988 starting July 15, 2026.)

### message_27 (user_37): facts

> Cedar Health telah memperbarui catatan penggajian Anda. Gaji rutin Anda untuk penggajian berikutnya adalah IDR 21090000. Penggajian yang sama mencakup penyesuaian tunggakan satu kali sebesar IDR 9490500. Slip gaji berikutnya akan menampilkan gaji rutin dan penyesuaian satu kali secara terpisah. Ref payroll EMP-0027.

- rules: `salary_with_arrears`; salary_amount 21,090,000 IDR scope=permanent
- model: `salary_with_arrears`; one_time_credit 9,490,500 IDR; salary_amount 21,090,000 IDR scope=next_only (Cedar Health has updated your payroll with a regular salary of IDR 21090000 for the next payroll, plus a one-time arrears adjustment of IDR 9490500.)

### message_29 (user_40): facts

> Your payroll details at BrightPath Media have changed. Your first salary will be EUR 1760. The confirmed credit date is 2024-06-15. The money will appear after the bank posts the credit. Payroll ref EMP-0029.

- rules: `first_salary`; salary_amount 1,760 EUR 2024-06-15 scope=permanent
- model: `first_salary`; salary_amount 1,760 EUR 2024-06-15 scope=permanent; salary_date 2024-06-15 (Your first salary from BrightPath Media of EUR 1760 is scheduled for 2024-06-15.)

### message_30 (user_42): facts

> A quick update from the payroll team at Greenfield Foods. One household employment record has ended. The remaining confirmed monthly salary is INR 148000. Any income that has ended should be removed from future estimates. Payroll ref EMP-0030.

- rules: `household_income_ended`; unconfirmed_income scope=secondary
- model: `household_income_ended`; income_stopped; salary_amount 148,000 INR scope=permanent (One household employment record has ended, leaving the remaining monthly salary at INR 148000.)

### message_31 (user_43): facts

> Ada pembaruan singkat dari tim payroll HarborWorks. Gaji pertama dari perusahaan baru adalah IDR 32870000. Pembayaran sudah dikonfirmasi untuk 2024-09-15. Pemrosesan bank dapat memerlukan waktu seperti biasa setelah gaji dikirim. Ref payroll EMP-0031.

- rules: `first_salary`; salary_amount 32,870,000 IDR 2024-09-15 scope=permanent
- model: `first_salary`; salary_amount 32,870,000 IDR 2024-09-15 scope=permanent; salary_date 2024-09-15 (First salary of IDR 32870000 from HarborWorks is confirmed for September 15, 2024.)

### message_32 (user_44): facts

> A note from Riverline Retail about your upcoming pay. Your first salary will be INR 115000. The confirmed credit date is 2025-02-15. The money will appear after the bank posts the credit. Payroll ref EMP-0032.

- rules: `first_salary`; salary_amount 115,000 INR 2025-02-15 scope=permanent
- model: `first_salary`; salary_amount 115,000 INR 2025-02-15 scope=permanent; salary_date 2025-02-15 (Your first salary from Riverline Retail of INR 115000 is scheduled for credit on 2025-02-15.)

### message_33 (user_45): facts

> Ada pembaruan singkat dari tim payroll Northstar Labs. Gaji bulanan Anda naik menjadi IDR 17290000. Perubahan ini berlaku mulai 2026-07-15. Jumlah yang diperbarui akan terlihat pada slip gaji berikutnya. Ref payroll EMP-0033.

- rules: `salary_increase`; salary_amount 17,290,000 IDR 2026-07-15 scope=permanent
- model: `salary_increase`; salary_amount 17,290,000 IDR 2026-07-15 scope=permanent; salary_date 2026-07-15 (Monthly salary at Northstar Labs increased to IDR 17,290,000 starting July 15, 2026.)

### message_37 (user_50): facts

> Greenfield Foods has updated your payroll record. One household employment record has ended. The remaining confirmed monthly salary is INR 126000. Any income that has ended should be removed from future estimates. Payroll ref EMP-0037.

- rules: `household_income_ended`; unconfirmed_income scope=secondary
- model: `household_income_ended`; income_stopped; salary_amount 126,000 INR scope=permanent (One household employment record has ended, leaving a confirmed monthly salary of INR 126000.)

### message_38 (user_52): facts

> HarborWorks has updated your payroll record. Your first salary from the new employer is INR 69000. It is confirmed for 2024-06-15. Bank processing may take the usual time after the payroll is released. Payroll ref EMP-0038.

- rules: `first_salary`; salary_amount 69,000 INR 2024-06-15 scope=permanent
- model: `first_salary`; salary_amount 69,000 INR 2024-06-15 scope=permanent; salary_date 2024-06-15 (Your first salary of INR 69000 from HarborWorks is confirmed for 2024-06-15.)

### message_41 (user_57): facts

> Here’s the latest transaction update from Summit Bank. The matching debit and credit came from a transfer between your two accounts. Both accounts are registered under the same account holder. Both entries will remain visible in your transaction history. Txn ref BAN-0041.

- rules: `internal_transfer`; internal_transfer
- model: `internal_transfer`; no facts (Notification regarding an internal transfer between two accounts belonging to the same holder.)

### message_42 (user_58): kind, facts

> Rincian penggajian Anda di Cedar Health telah berubah. Salah satu sumber pendapatan kerja rumah tangga telah berakhir. Sisa gaji bulanan yang dikonfirmasi adalah IDR 25840000. Pendapatan yang sudah berakhir harus dikeluarkan dari perkiraan berikutnya. Ref payroll EMP-0042.

- rules: `household_income_ended`; unconfirmed_income scope=secondary
- model: `salary_reduced_leave`; income_stopped; salary_amount 25,840,000 IDR scope=next_only (Penggajian di Cedar Health telah berubah dan salah satu sumber pendapatan telah berakhir, dengan sisa gaji bulanan yang dikonfirmasi sebesar IDR 25,840,000.)

### message_43 (user_59): facts

> A quick update about your WorkDash account. The next WorkDash payout is still pending. The weekly earnings shown in the WorkDash app can change until the payout is closed. The balance isn’t withdrawable until the payout shows as completed. Case ref SER-0043.

- rules: `gig_payout_pending`; unconfirmed_income scope=all_variable
- model: `gig_payout_pending`; unconfirmed_income scope=secondary (The next WorkDash payout is currently pending and not yet withdrawable.)

### message_46 (user_62): facts

> InvoiceFlow has new information about your next payment. The client approved an invoice payment of USD 2112. Settlement is expected on 2025-08-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0046.

- rules: `invoice_confirmed`; one_time_credit 2,112 USD 2025-08-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 2,112 USD 2025-08-15 (An invoice payment of USD 2112 has been approved by the client with settlement expected on 2025-08-15.)

### message_49 (user_66): facts

> PayPilot has new information about your next payment. The client approved an invoice payment of INR 116000. Settlement is expected on 2026-04-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0049.

- rules: `invoice_confirmed`; one_time_credit 116,000 INR 2026-04-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 116,000 INR 2026-04-15 (An invoice payment of INR 116000 is expected to settle on 2026-04-15.)

### message_53 (user_71): facts

> Greenfield Foods telah memperbarui catatan penggajian Anda. Gaji sebesar USD 696 dikonfirmasi untuk 2025-05-15. Bank penerima akan mengonversinya dengan kurs pada tanggal penyelesaian. Jumlah yang diterima dalam mata uang utama bergantung pada kurs tanggal penyelesaian. Ref payroll EMP-0053.

- rules: `international_salary`; salary_amount 696 USD 2025-05-15 scope=permanent
- model: `international_salary`; salary_amount 696 USD 2025-05-15 scope=next_only; salary_date 2025-05-15 (Greenfield Foods confirmed an international salary of USD 696 for 2025-05-15.)

### message_54 (user_72): facts

> Hi, Cedar Health payroll here. Your first salary will be ZAR 18700. The confirmed credit date is 2026-07-15. The money will appear after the bank posts the credit. Payroll ref EMP-0054.

- rules: `first_salary`; salary_amount 18,700 ZAR 2026-07-15 scope=permanent
- model: `first_salary`; salary_amount 18,700 ZAR 2026-07-15 scope=next_only; salary_date 2026-07-15 (Your first salary of ZAR 18700 is scheduled to be credited on 2026-07-15.)

### message_56 (user_74): facts

> Here’s the latest service update from PayPilot. The client approved an invoice payment of ZAR 26180. Settlement is expected on 2025-08-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0056.

- rules: `invoice_confirmed`; one_time_credit 26,180 ZAR 2025-08-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 26,180 ZAR 2025-08-15 (An invoice payment of ZAR 26180 has been approved and is expected to settle on 2025-08-15.)

### message_58 (user_76): facts

> Here’s the latest payroll information from BrightPath Media. Your confirmed base salary is USD 3072. The commission shown for open deals is still pending approval. Open deals will stay out of the payout until the commission is marked as earned. Payroll ref EMP-0058.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `commission_pending`; salary_amount 3,072 USD scope=permanent; unconfirmed_income scope=commission (Your confirmed base salary is USD 3072, while commission from open deals remains pending approval.)

### message_60 (user_80): facts

> Hi, Northstar Labs payroll here. Your confirmed base salary is INR 158000. The commission shown for open deals is still pending approval. Open deals will stay out of the payout until the commission is marked as earned. Payroll ref EMP-0060.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `commission_pending`; salary_amount 158,000 INR scope=permanent; unconfirmed_income scope=commission (Your base salary of INR 158000 is confirmed, but commission from open deals remains pending approval.)

### message_62 (user_82): facts

> Hi, Cedar Health payroll here. Your regular salary for the next payroll is USD 1752. The same payroll includes a one-time arrears adjustment of USD 788.40. Your next payslip will show the regular pay and any one-off adjustment separately. Payroll ref EMP-0062.

- rules: `salary_with_arrears`; salary_amount 1,752 USD scope=permanent
- model: `salary_with_arrears`; one_time_credit 788.4 USD; salary_amount 1,752 USD scope=next_only (Your upcoming payroll includes your regular salary and a one-time arrears adjustment.)

### message_63 (user_83): facts

> Hi, Cobalt Systems payroll here. Regular salary of INR 62000 resumes on 2025-05-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0063.

- rules: `salary_resumes`; salary_amount 62,000 INR 2025-05-15 scope=permanent
- model: `salary_resumes`; salary_amount 62,000 INR 2025-05-15 scope=permanent; salary_date 2025-05-15 (Regular salary of INR 62000 resumes on 2025-05-15.)

### message_66 (user_87): facts

> Hi, HarborWorks payroll here. Regular salary of INR 251000 resumes on 2026-01-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0066.

- rules: `salary_resumes`; salary_amount 251,000 INR 2026-01-15 scope=permanent
- model: `salary_resumes`; salary_amount 251,000 INR 2026-01-15 scope=permanent; salary_date 2026-01-15 (Regular salary of INR 251000 resumes on 2026-01-15.)

### message_68 (user_90): facts

> Hi, ClientDesk here. The client approved an invoice payment of ZAR 13420. Settlement is expected on 2026-07-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0068.

- rules: `invoice_confirmed`; one_time_credit 13,420 ZAR 2026-07-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 13,420 ZAR 2026-07-15 (An invoice payment of ZAR 13420 has been approved and is expected to settle on 2026-07-15.)

### message_70 (user_92): facts

> A quick update from the payroll team at Cobalt Systems. Your confirmed base salary is ZAR 49280. The commission shown for open deals is still pending approval. Open deals will stay out of the payout until the commission is marked as earned. Payroll ref EMP-0070.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `commission_pending`; salary_amount 49,280 ZAR scope=permanent; unconfirmed_income scope=commission (Your base salary is confirmed at ZAR 49280 while commission remains pending approval.)

### message_72 (user_94): facts

> Hi, ProjectPay here. The client approved an invoice payment of EUR 924. Settlement is expected on 2024-12-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0072.

- rules: `invoice_confirmed`; one_time_credit 924 EUR 2024-12-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 924 EUR 2024-12-15 (An invoice payment of EUR 924 has been approved by the client and is expected to settle on 2024-12-15.)

### message_74 (user_98): kind, facts

> Greenfield Foods payroll has posted a new update. Your salary of EUR 1804 is confirmed for 2025-08-15. The receiving bank will convert it using the rate applied on the settlement date. The amount received in your home currency will depend on the settlement-date conversion. Payroll ref EMP-0074.

- rules: `international_salary`; salary_amount 1,804 EUR 2025-08-15 scope=permanent
- model: `regular_salary_confirmed`; salary_amount 1,804 EUR 2025-08-15 scope=next_only; salary_date 2025-08-15 (A salary of EUR 1804 is confirmed for August 15, 2025.)

### message_76 (user_102): facts

> There’s a new account update from WorkPort. The client approved an invoice payment of EUR 1419. Settlement is expected on 2026-04-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0076.

- rules: `invoice_confirmed`; one_time_credit 1,419 EUR 2026-04-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 1,419 EUR 2026-04-15 (An invoice payment of EUR 1419 is confirmed with settlement expected on 2026-04-15.)

### message_78 (user_104): kind, facts

> Greenfield Foods has updated your payroll record. Your confirmed base salary is ZAR 35860. The commission shown for open deals is still pending approval. Open deals will stay out of the payout until the commission is marked as earned. Payroll ref EMP-0078.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `salary_increase`; salary_amount 35,860 ZAR scope=permanent; unconfirmed_income scope=commission (Greenfield Foods has updated your payroll record with a confirmed base salary of ZAR 35860, while commission for open deals remains pending.)

### message_80 (user_106): facts

> A quick update from the payroll team at HarborWorks. Your first salary from the new employer is EUR 1815. It is confirmed for 2024-12-15. Bank processing may take the usual time after the payroll is released. Payroll ref EMP-0080.

- rules: `first_salary`; salary_amount 1,815 EUR 2024-12-15 scope=permanent
- model: `first_salary`; salary_amount 1,815 EUR 2024-12-15 scope=permanent; salary_date 2024-12-15 (Your first salary of EUR 1815 from HarborWorks is confirmed for 2024-12-15.)

### message_81 (user_107): facts

> Greenfield Foods payroll has posted a new update. Your first salary of EUR 2123 is scheduled for 2025-05-15. Payroll has approved the payment and sent it for processing. The credit will show only after the bank processes the payroll file. Payroll ref EMP-0081.

- rules: `first_salary`; salary_amount 2,123 EUR 2025-05-15 scope=permanent
- model: `first_salary`; salary_amount 2,123 EUR 2025-05-15 scope=permanent; salary_date 2025-05-15 (Your first salary of EUR 2123 is scheduled for 2025-05-15.)

### message_82 (user_108): kind, facts

> Your payroll details at Riverline Retail have changed. Your confirmed base salary is ZAR 33440. The commission shown for open deals is still pending approval. Open deals will stay out of the payout until the commission is marked as earned. Payroll ref EMP-0082.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `salary_increase`; salary_amount 33,440 ZAR scope=permanent; unconfirmed_income scope=commission (Your base salary at Riverline Retail has been updated to ZAR 33440 while commission remains pending.)

### message_83 (user_110): facts

> WorkPort wanted to let you know about a change on your account. The client approved an invoice payment of ZAR 16720. Settlement is expected on 2025-08-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0083.

- rules: `invoice_confirmed`; one_time_credit 16,720 ZAR 2025-08-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 16,720 ZAR 2025-08-15 (An invoice payment of ZAR 16720 has been approved and is expected to settle on 2025-08-15.)

### message_85 (user_112): facts

> A note from BrightPath Media about your upcoming pay. Your first salary will be EUR 627. The confirmed credit date is 2024-06-15. The money will appear after the bank posts the credit. Payroll ref EMP-0085.

- rules: `first_salary`; salary_amount 627 EUR 2024-06-15 scope=permanent
- model: `first_salary`; salary_amount 627 EUR 2024-06-15 scope=next_only; salary_date 2024-06-15 (Your first salary of EUR 627 is confirmed to be credited on 2024-06-15.)

### message_86 (user_113): kind, facts

> MoneyHub account update: Your wallet was charged for the session at Charge Point 1110 in Krishnagiri on 3 September 2026 at 12:35 a.m. The receipt contains the final INR amount. Your employer has confirmed a USD 1296 salary credit for 15 September 2026. The salary will use the exchange rate when it settles. Account ref FIN-0086.

- rules: `receipt_confirmation`; salary_amount 1,296 USD 2026-09-15 scope=permanent
- model: `regular_salary_confirmed`; salary_amount 1,296 USD 2026-09-15 scope=next_only; salary_date 2026-09-15 (Your employer has confirmed a salary credit of USD 1296 for 15 September 2026.)

### message_90 (user_118): facts

> A note from Cedar Health about your upcoming pay. Your regular salary for the next payroll is EUR 759. The same payroll includes a one-time arrears adjustment of EUR 341.55. Your next payslip will show the regular pay and any one-off adjustment separately. Payroll ref EMP-0090.

- rules: `salary_with_arrears`; salary_amount 759 EUR scope=permanent
- model: `salary_with_arrears`; one_time_credit 341.55 EUR; salary_amount 759 EUR scope=next_only (Your upcoming payroll includes your regular salary of EUR 759 and a one-time arrears adjustment of EUR 341.55.)

### message_91 (user_119): facts

> A note from Cobalt Systems about your upcoming pay. Regular salary of INR 176000 resumes on 2025-05-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0091.

- rules: `salary_resumes`; salary_amount 176,000 INR 2025-05-15 scope=permanent
- model: `salary_resumes`; salary_amount 176,000 INR 2025-05-15 scope=permanent; salary_date 2025-05-15 (Regular salary of INR 176000 resumes on 2025-05-15.)

### message_93 (user_122): facts

> Ada pembaruan singkat untuk akun ProjectPay Anda. Klien menyetujui pembayaran faktur sebesar IDR 15390000. Penyelesaian diperkirakan pada 2025-08-15; faktur lain yang diajukan masih menunggu persetujuan. Hanya faktur yang sudah dikonfirmasi yang dapat dimasukkan dalam pembayaran berikutnya. Ref kasus SER-0093.

- rules: `invoice_confirmed`; one_time_credit 15,390,000 IDR 2025-08-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 15,390,000 IDR 2025-08-15 (Client approved an invoice payment of IDR 15390000 expected to settle on 2025-08-15.)

### message_95 (user_125): kind, facts

> A quick update from the payroll team at Greenfield Foods. Your salary of EUR 748 is confirmed for 2025-11-15. The receiving bank will convert it using the rate applied on the settlement date. The amount received in your home currency will depend on the settlement-date conversion. Payroll ref EMP-0095.

- rules: `international_salary`; salary_amount 748 EUR 2025-11-15 scope=permanent
- model: `regular_salary_confirmed`; salary_amount 748 EUR 2025-11-15 scope=next_only; salary_date 2025-11-15 (Your salary of EUR 748 is confirmed for 2025-11-15.)

### message_96 (user_126): facts

> ClientDesk has new information about your next payment. The client approved an invoice payment of INR 152000. Settlement is expected on 2026-07-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0096.

- rules: `invoice_confirmed`; one_time_credit 152,000 INR 2026-07-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 152,000 INR 2026-07-15 (An invoice payment of INR 152000 has been confirmed for settlement on 2026-07-15.)

### message_97 (user_127): facts

> Cedar Health payroll has posted a new update. Regular salary of ZAR 50160 resumes on 2024-09-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0097.

- rules: `salary_resumes`; salary_amount 50,160 ZAR 2024-09-15 scope=permanent
- model: `salary_resumes`; salary_amount 50,160 ZAR 2024-09-15 scope=permanent; salary_date 2024-09-15 (Regular salary of ZAR 50160 resumes on 2024-09-15.)

### message_104 (user_135): facts

> A note from Riverline Retail about your upcoming pay. Your monthly salary has increased to USD 828. The change applies from 2026-07-15. The revised amount will appear on your next payslip. Payroll ref EMP-0104.

- rules: `salary_increase`; salary_amount 828 USD 2026-07-15 scope=permanent
- model: `salary_increase`; salary_amount 828 USD 2026-07-15 scope=permanent; salary_date 2026-07-15 (Your monthly salary has increased to USD 828 starting from 2026-07-15.)

### message_107 (user_140): facts

> Hi, HarborWorks payroll here. Your first salary will be ZAR 31900. The confirmed credit date is 2025-02-15. The money will appear after the bank posts the credit. Payroll ref EMP-0107.

- rules: `first_salary`; salary_amount 31,900 ZAR 2025-02-15 scope=permanent
- model: `first_salary`; salary_amount 31,900 ZAR 2025-02-15 scope=permanent; salary_date 2025-02-15 (Your first salary of ZAR 31900 is confirmed to be credited on 2025-02-15.)

### message_109 (user_142): facts

> A note from InvoiceFlow about recent activity on your account. The client approved an invoice payment of USD 2040. Settlement is expected on 2024-12-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0109.

- rules: `invoice_confirmed`; one_time_credit 2,040 USD 2024-12-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 2,040 USD 2024-12-15 (An invoice payment of USD 2040 has been approved with settlement expected on 2024-12-15.)

### message_110 (user_143): kind

> ClaimDesk has shared an update about your funds. The prize proceeds have reached your account after withholding. The claim is now closed and there are no further scheduled payments. There won’t be another payment unless a separate prize is confirmed. Account ref FIN-0110.

- rules: `prize_received`; no facts
- model: `prize_scam`; no facts (Notification regarding prize proceeds and claim closure.)

### message_111 (user_144): facts

> A quick update from the payroll team at Cobalt Systems. Your first salary will be USD 864. The confirmed credit date is 2026-07-15. The money will appear after the bank posts the credit. Payroll ref EMP-0111.

- rules: `first_salary`; salary_amount 864 USD 2026-07-15 scope=permanent
- model: `first_salary`; salary_amount 864 USD 2026-07-15 scope=next_only; salary_date 2026-07-15 (Your first salary of USD 864 is confirmed to be credited on 2026-07-15.)

### message_112 (user_145): facts

> Hi, Cobalt Systems payroll here. Your regular salary for the next payroll is INR 258000. The same payroll includes a one-time arrears adjustment of INR 116100. Your next payslip will show the regular pay and any one-off adjustment separately. Payroll ref EMP-0112.

- rules: `salary_with_arrears`; salary_amount 258,000 INR scope=permanent
- model: `salary_with_arrears`; one_time_credit 116,100 INR; salary_amount 258,000 INR scope=next_only (Your next payroll includes a regular salary of INR 258000 and a one-time arrears adjustment of INR 116100.)

### message_113 (user_147): facts

> Hi, Greenfield Foods payroll here. Regular salary of EUR 1529 resumes on 2026-04-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0113.

- rules: `salary_resumes`; salary_amount 1,529 EUR 2026-04-15 scope=permanent
- model: `salary_resumes`; salary_amount 1,529 EUR 2026-04-15 scope=permanent; salary_date 2026-04-15 (Regular salary of EUR 1529 resumes on April 15, 2026.)

### message_116 (user_151): facts

> Halo, ini tim payroll Northstar Labs. Gaji pertama dari perusahaan baru adalah IDR 46170000. Pembayaran sudah dikonfirmasi untuk 2024-09-15. Pemrosesan bank dapat memerlukan waktu seperti biasa setelah gaji dikirim. Ref payroll EMP-0116.

- rules: `first_salary`; salary_amount 46,170,000 IDR 2024-09-15 scope=permanent
- model: `first_salary`; salary_amount 46,170,000 IDR 2024-09-15 scope=permanent; salary_date 2024-09-15 (First salary of IDR 46170000 from Northstar Labs is confirmed for 2024-09-15.)

### message_119 (user_154): facts

> Hi, Cedar Health payroll here. One household employment record has ended. The remaining confirmed monthly salary is EUR 1628. Any income that has ended should be removed from future estimates. Payroll ref EMP-0119.

- rules: `household_income_ended`; unconfirmed_income scope=secondary
- model: `household_income_ended`; income_stopped; salary_amount 1,628 EUR scope=permanent (One household employment record has ended, leaving a confirmed monthly salary of EUR 1628.)

### message_120 (user_155): facts

> A quick update from the payroll team at Greenfield Foods. Regular salary of INR 84000 resumes on 2025-05-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0120.

- rules: `salary_resumes`; salary_amount 84,000 INR 2025-05-15 scope=permanent
- model: `salary_resumes`; salary_amount 84,000 INR 2025-05-15 scope=permanent; salary_date 2025-05-15 (Regular salary of INR 84000 resumes on 2025-05-15.)

### message_124 (user_160): facts

> A quick update from the payroll team at Riverline Retail. Your first salary from the new employer is EUR 1672. It is confirmed for 2024-06-15. Bank processing may take the usual time after the payroll is released. Payroll ref EMP-0124.

- rules: `first_salary`; salary_amount 1,672 EUR 2024-06-15 scope=permanent
- model: `first_salary`; salary_amount 1,672 EUR 2024-06-15 scope=permanent; salary_date 2024-06-15 (Your first salary of EUR 1672 from Riverline Retail is confirmed for June 15, 2024.)

### message_125 (user_161): facts

> Ada informasi baru dari HarborWorks tentang gaji Anda. Gaji pertama Anda sebesar IDR 26790000 dijadwalkan pada 2025-11-15. Tim payroll sudah menyetujui pembayaran dan mengirimkannya untuk diproses. Dana baru akan terlihat setelah bank memproses berkas penggajian. Ref payroll EMP-0125.

- rules: `first_salary`; salary_amount 26,790,000 IDR 2025-11-15 scope=permanent
- model: `first_salary`; salary_amount 26,790,000 IDR 2025-11-15 scope=permanent; salary_date 2025-11-15 (Gaji pertama Anda dari HarborWorks sebesar IDR 26790000 dijadwalkan pada 2025-11-15.)

### message_127 (user_163): facts

> Greenfield Foods telah memperbarui catatan penggajian Anda. Gaji rutin Anda untuk penggajian berikutnya adalah IDR 30400000. Penggajian yang sama mencakup penyesuaian tunggakan satu kali sebesar IDR 13680000. Slip gaji berikutnya akan menampilkan gaji rutin dan penyesuaian satu kali secara terpisah. Ref payroll EMP-0127.

- rules: `salary_with_arrears`; salary_amount 30,400,000 IDR scope=permanent
- model: `salary_with_arrears`; one_time_credit 13,680,000 IDR; salary_amount 30,400,000 IDR scope=next_only (Greenfield Foods updated your payroll with a regular salary of IDR 30,400,000 for the next pay period and a one-time arrears adjustment of IDR 13,680,000.)

### message_128 (user_164): kind, facts

> BrightPath Media telah memperbarui catatan penggajian Anda. Gaji pokok yang dikonfirmasi adalah IDR 44270000. Komisi dari transaksi yang masih berjalan belum disetujui. Transaksi yang masih berjalan tidak masuk pembayaran sampai komisinya dinyatakan diperoleh. Ref payroll EMP-0128.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `salary_increase`; salary_amount 44,270,000 IDR scope=permanent; unconfirmed_income scope=commission (BrightPath Media updated your payroll with a confirmed base salary of IDR 44,270,000, while commission remains unapproved.)

### message_130 (user_166): facts

> InvoiceFlow wanted to let you know about a change on your account. The client approved an invoice payment of USD 2340. Settlement is expected on 2024-12-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0130.

- rules: `invoice_confirmed`; one_time_credit 2,340 USD 2024-12-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 2,340 USD 2024-12-15 (An invoice payment of USD 2340 was approved by the client with settlement expected on 2024-12-15.)

### message_135 (user_171): facts

> Cedar Bank has new information about one of your transactions. The matching debit and credit came from a transfer between your two accounts. Both accounts are registered under the same account holder. Both entries will remain visible in your transaction history. Txn ref BAN-0135.

- rules: `internal_transfer`; internal_transfer
- model: `internal_transfer`; no facts (Notification regarding an internal transfer between your two accounts.)

### message_137 (user_173): kind, facts

> Hi, Greenfield Foods payroll here. Your salary of USD 1284 is confirmed for 2025-11-15. The receiving bank will convert it using the rate applied on the settlement date. The amount received in your home currency will depend on the settlement-date conversion. Payroll ref EMP-0137.

- rules: `international_salary`; salary_amount 1,284 USD 2025-11-15 scope=permanent
- model: `regular_salary_confirmed`; salary_amount 1,284 USD 2025-11-15 scope=next_only; salary_date 2025-11-15 (Your salary of USD 1284 is confirmed for November 15, 2025.)

### message_139 (user_176): facts

> A note from Cedar Health about your upcoming pay. Your confirmed base salary is EUR 847. The commission shown for open deals is still pending approval. Open deals will stay out of the payout until the commission is marked as earned. Payroll ref EMP-0139.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `commission_pending`; salary_amount 847 EUR scope=next_only; unconfirmed_income scope=commission (Your confirmed base salary is EUR 847 while commission for open deals remains pending.)

### message_141 (user_178): facts

> A quick update about your FreelanceHub account. The client approved an invoice payment of EUR 2376. Settlement is expected on 2024-12-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0141.

- rules: `invoice_confirmed`; one_time_credit 2,376 EUR 2024-12-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 2,376 EUR 2024-12-15 (An invoice payment of EUR 2376 has been approved for settlement on 2024-12-15.)

### message_143 (user_180): facts

> Here’s the latest payroll information from Northstar Labs. Your first salary will be INR 59000. The confirmed credit date is 2026-07-15. The money will appear after the bank posts the credit. Payroll ref EMP-0143.

- rules: `first_salary`; salary_amount 59,000 INR 2026-07-15 scope=permanent
- model: `first_salary`; salary_amount 59,000 INR 2026-07-15 scope=permanent; salary_date 2026-07-15 (Your first salary of INR 59000 from Northstar Labs is scheduled to be credited on 2026-07-15.)

### message_145 (user_183): kind

> CartLane memiliki informasi baru tentang pembayaran atau pengembalian dana Anda. Tagihan dikenakan dalam mata uang asing. Bank Anda akan mengonfirmasi jumlah akhir dalam mata uang utama saat transaksi selesai. Jumlah akhir dalam mata uang utama menggunakan kurs saat transaksi selesai. Ref pesanan MER-0145.

- rules: `foreign_bill_pending`; no facts
- model: `unknown`; no facts (CartLane sent a notification regarding a foreign currency charge awaiting final confirmation.)

### message_149 (user_187): facts

> A note from BrightPath Media about your upcoming pay. Your first salary from the new employer is INR 145000. It is confirmed for 2024-09-15. Bank processing may take the usual time after the payroll is released. Payroll ref EMP-0149.

- rules: `first_salary`; salary_amount 145,000 INR 2024-09-15 scope=permanent
- model: `first_salary`; salary_amount 145,000 INR 2024-09-15 scope=permanent; salary_date 2024-09-15 (Your first salary of INR 145000 from BrightPath Media is confirmed for September 15, 2024.)

### message_156 (user_197): facts

> Cedar Health telah memperbarui catatan penggajian Anda. Gaji pertama Anda sebesar IDR 38190000 dijadwalkan pada 2025-11-15. Tim payroll sudah menyetujui pembayaran dan mengirimkannya untuk diproses. Dana baru akan terlihat setelah bank memproses berkas penggajian. Ref payroll EMP-0156.

- rules: `first_salary`; salary_amount 38,190,000 IDR 2025-11-15 scope=permanent
- model: `first_salary`; salary_amount 38,190,000 IDR 2025-11-15 scope=permanent; salary_date 2025-11-15 (Gaji pertama Anda sebesar IDR 38190000 dijadwalkan cair pada 2025-11-15.)

### message_162 (user_204): facts

> A quick update from the payroll team at BrightPath Media. Your first salary will be EUR 2024. The confirmed credit date is 2026-01-15. The money will appear after the bank posts the credit. Payroll ref EMP-0162.

- rules: `first_salary`; salary_amount 2,024 EUR 2026-01-15 scope=permanent
- model: `first_salary`; salary_amount 2,024 EUR 2026-01-15 scope=next_only; salary_date 2026-01-15 (Your first salary of EUR 2024 is scheduled to be credited on 2026-01-15.)

### message_170 (user_219): facts

> A quick update from the payroll team at BrightPath Media. Regular salary of EUR 1914 resumes on 2026-04-15. A new recurring childcare payment begins in the same month. The updated pay and deductions will appear from the next cycle. Payroll ref EMP-0170.

- rules: `salary_resumes`; salary_amount 1,914 EUR 2026-04-15 scope=permanent
- model: `salary_resumes`; salary_amount 1,914 EUR 2026-04-15 scope=permanent; salary_date 2026-04-15 (Regular salary of EUR 1914 resumes on April 15, 2026.)

### message_173 (user_222): facts

> There’s a new account update from ClientDesk. The client approved an invoice payment of ZAR 35200. Settlement is expected on 2026-01-15; the other submitted invoices are still awaiting approval. Only invoices marked as confirmed should be included in the upcoming payout. Case ref SER-0173.

- rules: `invoice_confirmed`; one_time_credit 35,200 ZAR 2026-01-15; unconfirmed_income scope=all_variable
- model: `invoice_confirmed`; one_time_credit 35,200 ZAR 2026-01-15 (An invoice payment of ZAR 35200 has been approved with settlement expected on 2026-01-15.)

### message_176 (user_226): facts

> Greenfield Foods has updated your payroll record. Your regular salary for the next payroll is INR 260000. The same payroll includes a one-time arrears adjustment of INR 117000. Your next payslip will show the regular pay and any one-off adjustment separately. Payroll ref EMP-0176.

- rules: `salary_with_arrears`; salary_amount 260,000 INR scope=permanent
- model: `salary_with_arrears`; one_time_credit 117,000 INR; salary_amount 260,000 INR scope=next_only (Your payroll record was updated with a regular salary of INR 260000 and a one-time arrears adjustment of INR 117000 for the next payroll.)

### message_178 (user_228): facts

> Ada informasi baru dari Northstar Labs tentang gaji Anda. Gaji pertama Anda sebesar IDR 30400000. Tanggal kredit yang dikonfirmasi adalah 2026-04-15. Dana akan terlihat setelah bank mencatat kreditnya. Ref payroll EMP-0178.

- rules: `first_salary`; salary_amount 30,400,000 IDR 2026-04-15 scope=permanent
- model: `first_salary`; salary_amount 30,400,000 IDR 2026-04-15 scope=permanent; salary_date 2026-04-15 (Gaji pertama Anda sebesar IDR 30.400.000 dikonfirmasi untuk tanggal 2026-04-15.)

### message_180 (user_230): facts

> Northstar Labs has updated your payroll record. One household employment record has ended. The remaining confirmed monthly salary is EUR 2827. Any income that has ended should be removed from future estimates. Payroll ref EMP-0180.

- rules: `household_income_ended`; unconfirmed_income scope=secondary
- model: `household_income_ended`; salary_amount 2,827 EUR scope=permanent (Household employment has ended, leaving a confirmed monthly salary of EUR 2827.)

### message_181 (user_232): facts

> A note from Cobalt Systems about your upcoming pay. Your first salary will be EUR 2772. The confirmed credit date is 2024-06-15. The money will appear after the bank posts the credit. Payroll ref EMP-0181.

- rules: `first_salary`; salary_amount 2,772 EUR 2024-06-15 scope=permanent
- model: `first_salary`; salary_amount 2,772 EUR 2024-06-15 scope=permanent; salary_date 2024-06-15 (Your first salary from Cobalt Systems of EUR 2772 is confirmed for 2024-06-15.)

### message_182 (user_233): facts

> Berikut informasi penggajian terbaru dari Northstar Labs. Gaji pertama Anda sebesar IDR 16910000 dijadwalkan pada 2025-11-15. Tim payroll sudah menyetujui pembayaran dan mengirimkannya untuk diproses. Dana baru akan terlihat setelah bank memproses berkas penggajian. Ref payroll EMP-0182.

- rules: `first_salary`; salary_amount 16,910,000 IDR 2025-11-15 scope=permanent
- model: `first_salary`; salary_amount 16,910,000 IDR 2025-11-15 scope=next_only; salary_date 2025-11-15 (Gaji pertama Anda sebesar IDR 16910000 dijadwalkan pada 2025-11-15.)

### message_187 (user_238): kind, facts

> Your payroll details at Northstar Labs have changed. One household employment record has ended. The remaining confirmed monthly salary is USD 912. Any income that has ended should be removed from future estimates. Payroll ref EMP-0187.

- rules: `household_income_ended`; unconfirmed_income scope=secondary
- model: `salary_reduced_leave`; income_stopped; salary_amount 912 USD scope=permanent (One household employment record ended and the remaining confirmed monthly salary is USD 912.)

### message_188 (user_240): facts

> Cobalt Systems payroll has posted a new update. Your first salary will be INR 62000. The confirmed credit date is 2026-01-15. The money will appear after the bank posts the credit. Payroll ref EMP-0188.

- rules: `first_salary`; salary_amount 62,000 INR 2026-01-15 scope=permanent
- model: `first_salary`; salary_amount 62,000 INR 2026-01-15 scope=permanent; salary_date 2026-01-15 (Your first salary of INR 62000 from Cobalt Systems is confirmed for credit on 2026-01-15.)

### message_190 (user_242): facts

> Hi, Riverline Retail payroll here. Your first salary of EUR 968 is scheduled for 2025-08-15. Payroll has approved the payment and sent it for processing. The credit will show only after the bank processes the payroll file. Payroll ref EMP-0190.

- rules: `first_salary`; salary_amount 968 EUR 2025-08-15 scope=permanent
- model: `first_salary`; salary_amount 968 EUR 2025-08-15 scope=next_only; salary_date 2025-08-15 (Your first salary of EUR 968 is scheduled for 2025-08-15.)

### message_191 (user_245): kind, facts

> A quick update from the payroll team at Cedar Health. Your salary of USD 1680 is confirmed for 2025-11-15. The receiving bank will convert it using the rate applied on the settlement date. The amount received in your home currency will depend on the settlement-date conversion. Payroll ref EMP-0191.

- rules: `international_salary`; salary_amount 1,680 USD 2025-11-15 scope=permanent
- model: `regular_salary_confirmed`; salary_amount 1,680 USD 2025-11-15 scope=next_only; salary_date 2025-11-15 (Your salary of USD 1680 is confirmed for November 15, 2025.)

### message_194 (user_248): facts

> Riverline Retail payroll has posted a new update. Your confirmed base salary is USD 1548. The commission shown for open deals is still pending approval. Open deals will stay out of the payout until the commission is marked as earned. Payroll ref EMP-0194.

- rules: `commission_pending`; unconfirmed_income scope=commission
- model: `commission_pending`; salary_amount 1,548 USD scope=permanent; unconfirmed_income scope=commission (Base salary of USD 1548 is confirmed, but commission from open deals remains pending approval.)

### message_196 (user_250): facts

> A note from Cobalt Systems about your upcoming pay. Your first salary from the new employer is ZAR 53680. It is confirmed for 2024-12-15. Bank processing may take the usual time after the payroll is released. Payroll ref EMP-0196.

- rules: `first_salary`; salary_amount 53,680 ZAR 2024-12-15 scope=permanent
- model: `first_salary`; salary_amount 53,680 ZAR 2024-12-15 scope=permanent; salary_date 2024-12-15 (Your first salary of ZAR 53680 from Cobalt Systems is confirmed for 2024-12-15.)

### message_200 (user_256): facts

> Hi, Riverline Retail payroll here. Your first salary will be INR 214000. The confirmed credit date is 2024-06-15. The money will appear after the bank posts the credit. Payroll ref EMP-0200.

- rules: `first_salary`; salary_amount 214,000 INR 2024-06-15 scope=permanent
- model: `first_salary`; salary_amount 214,000 INR 2024-06-15 scope=permanent; salary_date 2024-06-15 (Your first salary of INR 214000 is confirmed to be credited on 2024-06-15.)

### message_202 (user_261): facts

> Hi, Summit Bank here. The matching debit and credit came from a transfer between your two accounts. Both accounts are registered under the same account holder. Both entries will remain visible in your transaction history. Txn ref BAN-0202.

- rules: `internal_transfer`; internal_transfer
- model: `internal_transfer`; no facts (Notification regarding an internal transfer between two accounts of the same holder.)

### message_203 (user_262): facts

> Tim payroll Cedar Health telah mengirim pembaruan. Salah satu sumber pendapatan kerja rumah tangga telah berakhir. Sisa gaji bulanan yang dikonfirmasi adalah IDR 48260000. Pendapatan yang sudah berakhir harus dikeluarkan dari perkiraan berikutnya. Ref payroll EMP-0203.

- rules: `household_income_ended`; unconfirmed_income scope=secondary
- model: `household_income_ended`; income_stopped; salary_amount 48,260,000 IDR scope=permanent (Household income has ended and the confirmed remaining monthly salary is IDR 48260000.)

### message_204 (user_263): facts

> Your payroll details at Riverline Retail have changed. Your salary of EUR 1485 is confirmed for 2025-05-15. The receiving bank will convert it using the rate applied on the settlement date. The amount received in your home currency will depend on the settlement-date conversion. Payroll ref EMP-0204.

- rules: `international_salary`; salary_amount 1,485 EUR 2025-05-15 scope=permanent
- model: `international_salary`; salary_amount 1,485 EUR 2025-05-15 scope=permanent; salary_date 2025-05-15 (Your international salary of EUR 1485 is confirmed for 2025-05-15.)

### message_210 (user_269): facts

> Your payroll details at Northstar Labs have changed. Your first salary of ZAR 38280 is scheduled for 2025-11-15. Payroll has approved the payment and sent it for processing. The credit will show only after the bank processes the payroll file. Payroll ref EMP-0210.

- rules: `first_salary`; salary_amount 38,280 ZAR 2025-11-15 scope=permanent
- model: `first_salary`; salary_amount 38,280 ZAR 2025-11-15 scope=permanent; salary_date 2025-11-15 (Your first salary of ZAR 38280 from Northstar Labs is scheduled for 2025-11-15.)

### message_211 (user_271): facts

> Hi, Greenfield Foods payroll here. Your regular salary for the next payroll is INR 103000. The same payroll includes a one-time arrears adjustment of INR 46350. Your next payslip will show the regular pay and any one-off adjustment separately. Payroll ref EMP-0211.

- rules: `salary_with_arrears`; salary_amount 103,000 INR scope=permanent
- model: `salary_with_arrears`; one_time_credit 46,350 INR; salary_amount 103,000 INR scope=next_only (Your next payroll includes a regular salary of INR 103000 and a one-time arrears adjustment of INR 46350.)

### message_213 (user_273): facts

> Harbor Bank telah meninjau transaksi pada rekening Anda. Debit dan kredit dengan jumlah yang sama berasal dari transfer antara dua rekening Anda. Kedua rekening terdaftar atas nama pemilik yang sama. Kedua transaksi akan tetap terlihat dalam riwayat rekening Anda. Ref transaksi BAN-0213.

- rules: `internal_transfer`; internal_transfer
- model: `internal_transfer`; no facts (The bank verified that a matching debit and credit represent an internal transfer between accounts owned by the same person.)

## Every message

| message_id | user_id | result | rules kind | model kind |
|---|---|---|---|---|
| message_01 | user_02 | agree | salary_increase | salary_increase |
| message_02 | user_03 | agree | regular_salary_confirmed | regular_salary_confirmed |
| message_03 | user_04 | agree | bonus_pending | bonus_pending |
| message_04 | user_06 | agree | temporary_pay | temporary_pay |
| message_05 | user_07 | agree | salary_date_moved | salary_date_moved |
| message_06 | user_08 | agree | salary_reduced_leave | salary_reduced_leave |
| message_07 | user_10 | agree | gig_payout_pending | gig_payout_pending |
| message_08 | user_11 | disagree (facts) | commission_pending | commission_pending |
| message_09 | user_12 | agree | seasonal_ended | seasonal_ended |
| message_10 | user_14 | disagree (facts) | salary_resumes | salary_resumes |
| message_11 | user_15 | disagree (facts) | first_salary | first_salary |
| message_12 | user_16 | agree | rent_increase | rent_increase |
| message_13 | user_18 | disagree (facts) | internal_transfer | internal_transfer |
| message_14 | user_20 | agree | refund_pending | refund_pending |
| message_15 | user_22 | agree | portfolio_value | portfolio_value |
| message_16 | user_23 | agree | prize_pending | prize_pending |
| message_17 | user_24 | disagree (kind) | prize_received | prize_scam |
| message_18 | user_26 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_19 | user_27 | agree | gig_payout_pending | gig_payout_pending |
| message_20 | user_28 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_21 | user_29 | agree | seasonal_ended | seasonal_ended |
| message_22 | user_32 | disagree (facts) | first_salary | first_salary |
| message_23 | user_33 | disagree (facts) | internal_transfer | internal_transfer |
| message_24 | user_34 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_25 | user_35 | agree | refund_pending | refund_pending |
| message_26 | user_36 | disagree (facts) | salary_increase | salary_increase |
| message_27 | user_37 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_28 | user_38 | agree | prize_received | prize_received |
| message_29 | user_40 | disagree (facts) | first_salary | first_salary |
| message_30 | user_42 | disagree (facts) | household_income_ended | household_income_ended |
| message_31 | user_43 | disagree (facts) | first_salary | first_salary |
| message_32 | user_44 | disagree (facts) | first_salary | first_salary |
| message_33 | user_45 | disagree (facts) | salary_increase | salary_increase |
| message_34 | user_47 | agree | gig_payout_pending | gig_payout_pending |
| message_35 | user_48 | agree | receipt_confirmation | receipt_confirmation |
| message_36 | user_49 | agree | temporary_pay | temporary_pay |
| message_37 | user_50 | disagree (facts) | household_income_ended | household_income_ended |
| message_38 | user_52 | disagree (facts) | first_salary | first_salary |
| message_39 | user_53 | agree | refund_pending | refund_pending |
| message_40 | user_54 | agree | salary_increase | salary_increase |
| message_41 | user_57 | disagree (facts) | internal_transfer | internal_transfer |
| message_42 | user_58 | disagree (kind, facts) | household_income_ended | salary_reduced_leave |
| message_43 | user_59 | disagree (facts) | gig_payout_pending | gig_payout_pending |
| message_44 | user_60 | agree | salary_reduced_leave | salary_reduced_leave |
| message_45 | user_61 | agree | seasonal_ended | seasonal_ended |
| message_46 | user_62 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_47 | user_64 | agree | refund_pending | refund_pending |
| message_48 | user_65 | agree | bonus_pending | bonus_pending |
| message_49 | user_66 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_50 | user_68 | agree | salary_date_moved | salary_date_moved |
| message_51 | user_69 | agree | rent_increase | rent_increase |
| message_52 | user_70 | agree | portfolio_value | portfolio_value |
| message_53 | user_71 | disagree (facts) | international_salary | international_salary |
| message_54 | user_72 | disagree (facts) | first_salary | first_salary |
| message_55 | user_73 | agree | rent_increase | rent_increase |
| message_56 | user_74 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_57 | user_75 | agree | employment_ended | employment_ended |
| message_58 | user_76 | disagree (facts) | commission_pending | commission_pending |
| message_59 | user_77 | agree | refund_pending | refund_pending |
| message_60 | user_80 | disagree (facts) | commission_pending | commission_pending |
| message_61 | user_81 | agree | rent_increase | rent_increase |
| message_62 | user_82 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_63 | user_83 | disagree (facts) | salary_resumes | salary_resumes |
| message_64 | user_84 | agree | receipt_confirmation | receipt_confirmation |
| message_65 | user_85 | agree | temporary_pay | temporary_pay |
| message_66 | user_87 | disagree (facts) | salary_resumes | salary_resumes |
| message_67 | user_88 | agree | prize_scam | prize_scam |
| message_68 | user_90 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_69 | user_91 | agree | failed_debit_retry | failed_debit_retry |
| message_70 | user_92 | disagree (facts) | commission_pending | commission_pending |
| message_71 | user_93 | agree | prize_pending | prize_pending |
| message_72 | user_94 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_73 | user_95 | agree | salary_date_moved | salary_date_moved |
| message_74 | user_98 | disagree (kind, facts) | international_salary | regular_salary_confirmed |
| message_75 | user_101 | agree | prize_received | prize_received |
| message_76 | user_102 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_77 | user_103 | agree | temporary_pay | temporary_pay |
| message_78 | user_104 | disagree (kind, facts) | commission_pending | salary_increase |
| message_79 | user_105 | agree | portfolio_value | portfolio_value |
| message_80 | user_106 | disagree (facts) | first_salary | first_salary |
| message_81 | user_107 | disagree (facts) | first_salary | first_salary |
| message_82 | user_108 | disagree (kind, facts) | commission_pending | salary_increase |
| message_83 | user_110 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_84 | user_111 | agree | employment_ended | employment_ended |
| message_85 | user_112 | disagree (facts) | first_salary | first_salary |
| message_86 | user_113 | disagree (kind, facts) | receipt_confirmation | regular_salary_confirmed |
| message_87 | user_114 | agree | salary_reduced_leave | salary_reduced_leave |
| message_88 | user_115 | agree | prize_received | prize_received |
| message_89 | user_117 | agree | salary_increase | salary_increase |
| message_90 | user_118 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_91 | user_119 | disagree (facts) | salary_resumes | salary_resumes |
| message_92 | user_120 | agree | investment_sale_settled | investment_sale_settled |
| message_93 | user_122 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_94 | user_123 | agree | gig_payout_pending | gig_payout_pending |
| message_95 | user_125 | disagree (kind, facts) | international_salary | regular_salary_confirmed |
| message_96 | user_126 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_97 | user_127 | disagree (facts) | salary_resumes | salary_resumes |
| message_98 | user_128 | agree | bonus_pending | bonus_pending |
| message_99 | user_129 | agree | prize_received | prize_received |
| message_100 | user_130 | agree | temporary_pay | temporary_pay |
| message_101 | user_131 | agree | salary_date_moved | salary_date_moved |
| message_102 | user_132 | agree | salary_reduced_leave | salary_reduced_leave |
| message_103 | user_133 | agree | seasonal_ended | seasonal_ended |
| message_104 | user_135 | disagree (facts) | salary_increase | salary_increase |
| message_105 | user_137 | agree | rent_increase | rent_increase |
| message_106 | user_138 | agree | duplicate_charge_dispute | duplicate_charge_dispute |
| message_107 | user_140 | disagree (facts) | first_salary | first_salary |
| message_108 | user_141 | agree | investment_sale_settled | investment_sale_settled |
| message_109 | user_142 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_110 | user_143 | disagree (kind) | prize_received | prize_scam |
| message_111 | user_144 | disagree (facts) | first_salary | first_salary |
| message_112 | user_145 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_113 | user_147 | disagree (facts) | salary_resumes | salary_resumes |
| message_114 | user_148 | agree | investment_sale_settled | investment_sale_settled |
| message_115 | user_150 | agree | salary_reduced_leave | salary_reduced_leave |
| message_116 | user_151 | disagree (facts) | first_salary | first_salary |
| message_117 | user_152 | agree | reimbursement | reimbursement |
| message_118 | user_153 | agree | foreign_bill_pending | foreign_bill_pending |
| message_119 | user_154 | disagree (facts) | household_income_ended | household_income_ended |
| message_120 | user_155 | disagree (facts) | salary_resumes | salary_resumes |
| message_121 | user_156 | agree | duplicate_charge_dispute | duplicate_charge_dispute |
| message_122 | user_157 | agree | temporary_pay | temporary_pay |
| message_123 | user_159 | agree | gig_payout_pending | gig_payout_pending |
| message_124 | user_160 | disagree (facts) | first_salary | first_salary |
| message_125 | user_161 | disagree (facts) | first_salary | first_salary |
| message_126 | user_162 | agree | salary_increase | salary_increase |
| message_127 | user_163 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_128 | user_164 | disagree (kind, facts) | commission_pending | salary_increase |
| message_129 | user_165 | agree | employment_ended | employment_ended |
| message_130 | user_166 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_131 | user_167 | agree | salary_date_moved | salary_date_moved |
| message_132 | user_168 | agree | salary_reduced_leave | salary_reduced_leave |
| message_133 | user_169 | agree | refund_pending | refund_pending |
| message_134 | user_170 | agree | prize_pending | prize_pending |
| message_135 | user_171 | disagree (facts) | internal_transfer | internal_transfer |
| message_136 | user_172 | agree | two_card_minimums | two_card_minimums |
| message_137 | user_173 | disagree (kind, facts) | international_salary | regular_salary_confirmed |
| message_138 | user_175 | agree | temporary_pay | temporary_pay |
| message_139 | user_176 | disagree (facts) | commission_pending | commission_pending |
| message_140 | user_177 | agree | salary_reduced_leave | salary_reduced_leave |
| message_141 | user_178 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_142 | user_179 | agree | prize_scam | prize_scam |
| message_143 | user_180 | disagree (facts) | first_salary | first_salary |
| message_144 | user_182 | agree | bonus_pending | bonus_pending |
| message_145 | user_183 | disagree (kind) | foreign_bill_pending | unknown |
| message_146 | user_184 | agree | refund_pending | refund_pending |
| message_147 | user_185 | agree | rent_increase | rent_increase |
| message_148 | user_186 | agree | salary_reduced_leave | salary_reduced_leave |
| message_149 | user_187 | disagree (facts) | first_salary | first_salary |
| message_150 | user_188 | agree | reimbursement | reimbursement |
| message_151 | user_189 | agree | salary_increase | salary_increase |
| message_152 | user_191 | agree | refund_pending | refund_pending |
| message_153 | user_193 | agree | temporary_pay | temporary_pay |
| message_154 | user_194 | agree | salary_date_moved | salary_date_moved |
| message_155 | user_195 | agree | salary_reduced_leave | salary_reduced_leave |
| message_156 | user_197 | disagree (facts) | first_salary | first_salary |
| message_157 | user_198 | agree | duplicate_charge_dispute | duplicate_charge_dispute |
| message_158 | user_199 | agree | gig_payout_pending | gig_payout_pending |
| message_159 | user_200 | agree | bonus_pending | bonus_pending |
| message_160 | user_201 | agree | seasonal_ended | seasonal_ended |
| message_161 | user_202 | agree | two_card_minimums | two_card_minimums |
| message_162 | user_204 | disagree (facts) | first_salary | first_salary |
| message_163 | user_208 | agree | portfolio_value | portfolio_value |
| message_164 | user_210 | agree | duplicate_charge_dispute | duplicate_charge_dispute |
| message_165 | user_212 | agree | salary_date_moved | salary_date_moved |
| message_166 | user_213 | agree | seasonal_ended | seasonal_ended |
| message_167 | user_214 | agree | refund_pending | refund_pending |
| message_168 | user_215 | agree | gig_payout_pending | gig_payout_pending |
| message_169 | user_216 | agree | salary_increase | salary_increase |
| message_170 | user_219 | disagree (facts) | salary_resumes | salary_resumes |
| message_171 | user_220 | agree | temporary_pay | temporary_pay |
| message_172 | user_221 | agree | refund_pending | refund_pending |
| message_173 | user_222 | disagree (facts) | invoice_confirmed | invoice_confirmed |
| message_174 | user_224 | agree | reimbursement | reimbursement |
| message_175 | user_225 | agree | rent_increase | rent_increase |
| message_176 | user_226 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_177 | user_227 | agree | bonus_pending | bonus_pending |
| message_178 | user_228 | disagree (facts) | first_salary | first_salary |
| message_179 | user_229 | agree | failed_debit_retry | failed_debit_retry |
| message_180 | user_230 | disagree (facts) | household_income_ended | household_income_ended |
| message_181 | user_232 | disagree (facts) | first_salary | first_salary |
| message_182 | user_233 | disagree (facts) | first_salary | first_salary |
| message_183 | user_234 | agree | duplicate_charge_dispute | duplicate_charge_dispute |
| message_184 | user_235 | agree | refund_pending | refund_pending |
| message_185 | user_236 | agree | portfolio_value | portfolio_value |
| message_186 | user_237 | agree | seasonal_ended | seasonal_ended |
| message_187 | user_238 | disagree (kind, facts) | household_income_ended | salary_reduced_leave |
| message_188 | user_240 | disagree (facts) | first_salary | first_salary |
| message_189 | user_241 | agree | seasonal_ended | seasonal_ended |
| message_190 | user_242 | disagree (facts) | first_salary | first_salary |
| message_191 | user_245 | disagree (kind, facts) | international_salary | regular_salary_confirmed |
| message_192 | user_246 | agree | employment_ended | employment_ended |
| message_193 | user_247 | agree | temporary_pay | temporary_pay |
| message_194 | user_248 | disagree (facts) | commission_pending | commission_pending |
| message_195 | user_249 | agree | salary_reduced_leave | salary_reduced_leave |
| message_196 | user_250 | disagree (facts) | first_salary | first_salary |
| message_197 | user_252 | agree | duplicate_charge_dispute | duplicate_charge_dispute |
| message_198 | user_253 | agree | failed_debit_retry | failed_debit_retry |
| message_199 | user_254 | agree | bonus_pending | bonus_pending |
| message_200 | user_256 | disagree (facts) | first_salary | first_salary |
| message_201 | user_259 | agree | failed_debit_retry | failed_debit_retry |
| message_202 | user_261 | disagree (facts) | internal_transfer | internal_transfer |
| message_203 | user_262 | disagree (facts) | household_income_ended | household_income_ended |
| message_204 | user_263 | disagree (facts) | international_salary | international_salary |
| message_205 | user_264 | agree | portfolio_value | portfolio_value |
| message_206 | user_265 | agree | seasonal_ended | seasonal_ended |
| message_207 | user_266 | agree | portfolio_value | portfolio_value |
| message_208 | user_267 | agree | foreign_bill_pending | foreign_bill_pending |
| message_209 | user_268 | agree | prize_pending | prize_pending |
| message_210 | user_269 | disagree (facts) | first_salary | first_salary |
| message_211 | user_271 | disagree (facts) | salary_with_arrears | salary_with_arrears |
| message_212 | user_272 | agree | bonus_pending | bonus_pending |
| message_213 | user_273 | disagree (facts) | internal_transfer | internal_transfer |
| message_214 | user_274 | agree | refund_pending | refund_pending |
| message_215 | user_275 | agree | refund_pending | refund_pending |
