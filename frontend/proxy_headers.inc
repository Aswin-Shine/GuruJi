# Shared reverse-proxy directives for /api/*. Everything EXCEPT proxy_pass —
# that line has to differ per location (see nginx.conf) because the two
# locations match prefixes of different lengths, so a single shared proxy_pass
# can't strip the right amount for both. Everything below this must stay
# identical between them, which is exactly what a shared include still buys us.

proxy_http_version 1.1;
proxy_set_header Connection "";          # required for upstream keepalive

proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;

# A tutoring turn is a synchronous moderation + RAG + LLM call. The backend's
# own OpenAI client uses a 10s timeout with one retry, so ~21s is the documented
# worst case; 45s leaves margin without letting a wedged upstream hold sockets
# open indefinitely.
proxy_connect_timeout 5s;
proxy_send_timeout    45s;
proxy_read_timeout    45s;

# Stream the response straight through. Buffering a 200-byte JSON reply only
# adds latency, and disabling it keeps nginx from writing to a temp path.
proxy_buffering off;
proxy_request_buffering on;

# Do not silently replay a paid, non-idempotent LLM call against a second
# upstream. If the backend errors, the user sees an honest failure.
proxy_next_upstream off;

# Strip anything the app server might leak about itself.
proxy_hide_header X-Powered-By;
proxy_hide_header Server;

# API responses are per-user and must never land in a shared cache.
add_header Cache-Control "no-store" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "no-referrer" always;
