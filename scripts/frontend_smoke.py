"""CIOS frontend smoke — STATIC assertions (no JS runtime at $0).

V2-DESIGN.md §C2: fails the build if any invariant is violated.
Honest limit: ye behavioral tests nahi hain — sirf static checks.
Manual QA matrix TEST-REPORT.md me hai.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
JS = (BASE / "frontend" / "app.js").read_text()
HTML = (BASE / "frontend" / "index.html").read_text()
CSS = (BASE / "frontend" / "styles.css").read_text()

failures = []


def check(name, cond, hint=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {hint}" if hint and not cond else ""))
    if not cond:
        failures.append(name)


def strip_line_comments(src):
    return "\n".join(
        line.split("//", 1)[0] if "://" not in line else line for line in src.splitlines()
    )


js_code = strip_line_comments(JS)

# 1. Exactly one network-call site: the unified CIOSApi.api wrapper.
check("single-fetch-wrapper", len(re.findall(r"(?<![\w.])fetch\(", js_code)) == 1,
      "app.js me sirf ek fetch( hona chahiye (CIOSApi.api ke andar)")
check("CIOSApi-exposed", "window.CIOSApi" in JS, "window.CIOSApi global missing")
check("CIOSApp-exposed", "window.CIOSApp" in JS, "window.CIOSApp global missing (setBtn/closeStudio)")

# 2. AbortController timeouts.
check("abort-controller", "AbortController" in JS, "AbortController missing")

# 3. Zero alert().
check("no-alert", "alert(" not in js_code, "alert( mila — toasts use karo")

# 4. Retry policy marker: GET only, max 1, network-level throws only.
check("retry-policy-marker", "RETRY-POLICY" in JS and "max 1 retry" in JS,
      "retry policy comment missing")
check("no-post-retry", "POST kabhi auto-retry nahi" in JS or "POST KABHI auto-retry nahi" in JS,
      "POST no-retry rule comment missing")

# 5. X-Request-ID header read on every response.
check("request-id-read", 'get("X-Request-ID")' in JS or "get('X-Request-ID')" in JS,
      "X-Request-ID response header read missing")
check("request-id-in-toast", "support ke liye ye ID bhejo" in JS,
      "request-id toast copy missing")

# 6. Toast system present.
check("toast-stack-html", 'id="toast-stack"' in HTML, "#toast-stack missing in index.html")
check("toast-css", "#toast-stack" in CSS, "#toast-stack CSS missing")
check("toast-js", "function toast(" in JS, "toast() function missing")

# 7. #report-print must be a DIRECT child of <body> (print-hiding selector).
class BodyChildParser(HTMLParser):
    VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.report_parent = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "body":
            self.stack = ["body"]
            return
        if self.stack:
            if attrs.get("id") == "report-print":
                self.report_parent = self.stack[-1]
            if tag not in self.VOID:
                self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

p = BodyChildParser()
p.feed(HTML)
check("report-print-direct-body-child", p.report_parent == "body",
      f"#report-print ka parent '{p.report_parent}' hai, body hona chahiye")

# 8. Print CSS.
check("media-print", "@media print" in CSS, "@media print missing in styles.css")
check("print-hiding-selector", "body.printing > :not(#report-print)" in CSS,
      "print hiding selector missing")

# 9. CSP: no inline handlers.
check("no-inline-handlers", "onclick=" not in HTML, "onclick= mila — CSP unsafe")

# 10. v2 API prefix: no old /api/ (non-v1) fetch paths left.
old_paths = [m for m in re.findall(r'"/api/(?!v1/)[a-z][^"]*"', JS)]
check("v1-prefix-only", not old_paths, f"purane /api/* paths baqi: {old_paths[:5]}")

# 11. Studio shell contract (exact IDs).
for el in ['id="studio-media"', 'id="studio-avatar"', 'id="nav-media"', 'id="nav-avatar"',
           "/static/media-studio.js", "/static/avatar-studio.js",
           "/static/media-studio.css", "/static/avatar-studio.css"]:
    check(f"studio-shell:{el}", el in HTML, f"{el} missing in index.html")

# 12. PDF buttons per feature tab.
for f in ["ideas", "research", "scripts", "packaging", "seo", "niche"]:
    check(f"print-button:{f}", f'id="print-{f}"' in HTML, f"#print-{f} missing")

print()
if failures:
    print(f"{len(failures)} FAILURES — build rok do.")
    sys.exit(1)
print("Sab static checks pass (static only — behavioral QA manual hai).")
