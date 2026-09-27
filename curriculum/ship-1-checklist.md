# Ship #1 checklist: Health Coach

Updated 2026-09-27. Code is publish-ready (bug fixes, UI polish, password gate, deploy config). What remains is accounts and days.

- [x] All 7 tools implemented and verified live (log_weight + save_plan added 2026-09-08)
- [x] .gitignore protects .env, .venv, data/, and the local-only folders (brain/, outreach/, .claude/ …)
- [x] README rewritten for GitHub; LICENSE (MIT); WRITEUP.md drafted
- [x] **YouTube key**: working `AIza` key in `apps/health-coach/.env`, verified with a live search + in-app playback (2026-09-27). `AQ.` keys (AI Studio / service-account-bound) get a 401 from YouTube.
- [x] Deploy-ready: password gate, `DATA_DIR` volume support, `TZ`, Procfile + railway.json, pinned requirements, iPhone icons (2026-09-27)
- [ ] **Sign in to GitHub once**: `gh auth login --web --git-protocol https` (gh is installed in ~/.local/bin)
- [ ] **Commit + push** the repo (public), then add the link below
- [ ] **Deploy on Railway** (README → "Put it on your phone"): root dir `apps/health-coach`, variables, volume at `/data`, generate domain
- [ ] **Install on iPhone**: Safari → sign in → Share → Add to Home Screen
- [ ] **Use the coach 3 days**: weigh in, log a meal, lock a plan each day
- [ ] **Edit WRITEUP.md** in your voice, then post it (LinkedIn or X) with the repo link
- [ ] **Log the ship** in `curriculum/log.md` (press Ship check on the HQ dashboard, or `/ship health-coach <repo url>`)
