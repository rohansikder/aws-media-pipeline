"""Wait for the local AWS API without relying on image-specific health routes."""

import time
import urllib.error
import urllib.request

for attempt in range(60):
    try:
        request = urllib.request.Request("http://localhost:4566/", method="GET")
        with urllib.request.urlopen(request, timeout=2):
            pass
        break
    except urllib.error.HTTPError:
        break  # An AWS error response still proves the listener is ready.
    except (OSError, TimeoutError):
        time.sleep(1)
else:
    raise SystemExit("Floci did not become ready within 60 seconds; inspect docker compose logs")
print("Floci API is accepting connections")
