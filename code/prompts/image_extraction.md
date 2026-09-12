The image is a receipt, invoice, bill or payslip attached to one financial event whose amount is missing. Read the single amount that the event represents (net pay for a payslip; the balance due for an outstanding bill; the total paid for a receipt).

Return ONLY JSON: {"amount": <number or null>, "currency": "<ISO code or null>", "evidence": "<the line you read it from>"}.
Never guess: if the total cannot be read, return {"amount": null, "currency": null, "evidence": "unreadable"}.
