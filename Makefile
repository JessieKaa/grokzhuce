run-turnstile-solver:
	uv run api_solver.py --browser_type camoufox --thread 5 --debug

run-go-imap:
	cd go_imap_service && go run main.go

run-grok:
	uv run grok.py --debug -n 5 --single 