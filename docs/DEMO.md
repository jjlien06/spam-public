# Optional live application demo

The optional `./demo.command` launcher is **not an offline sample**. It uses your own private profile and a selected real application. Filling can send or autosave information to an employer before Submit. For a no-account demonstration, use the local HTML fixtures in `tests/` instead.

To configure the live launcher after your own setup, create the ignored `me/coffee-chat-demo.json` (or the equivalent file under `SPAM_HOME`):

```json
{
  "job_id": "YOUR_EXISTING_QUEUE_ID",
  "url": "https://employer.example/your-selected-posting"
}
```

The ID must exist in your private queue and the URL must match that row exactly. A saved resume must exist at the path in your private profile. The source release does not include a selected job, resume, profile, or a claim that a real application has been rehearsed.

`./demo.command --check` runs local preflight checks. Without `--check`, the launcher opens the loopback control page, normally `http://127.0.0.1:8787`:

- **Start live fill** enters your saved information on the selected employer form and stops for review.
- **Watch application** focuses that employer's browser window.
- **Review & approve** can submit a real application after explicit approval. Do not approve unless you intend that external action.

Keep the employer window open for unknown questions, CAPTCHA, verification, or uncertain outcomes. Do not retry a possibly submitted application or bypass a website's restrictions. This optional launcher is experimental, not a guarantee of support for any particular employer.
