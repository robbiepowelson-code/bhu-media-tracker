// Edge Middleware — HTTP Basic Auth for the whole app.
// The password comes from the DASH_PASSWORD environment variable (set it in
// Vercel: Project → Settings → Environment Variables). Until it's set, the
// site stays locked to everyone. Username can be anything (we suggest "bhu").
// Everything requires the password EXCEPT /feed/* — a sanitized,
// contact-info-free data feed consumed by the public BHU Streamlit page.
export const config = { matcher: "/((?!feed/).*)" };

export default function middleware(request) {
  const expected = process.env.DASH_PASSWORD || "";
  const auth = request.headers.get("authorization") || "";

  if (expected && auth.startsWith("Basic ")) {
    try {
      const decoded = atob(auth.slice(6));
      const password = decoded.slice(decoded.indexOf(":") + 1);
      // constant-time-ish comparison
      if (
        password.length === expected.length &&
        [...password].every((ch, i) => ch === expected[i])
      ) {
        return; // authorized — serve the site
      }
    } catch (e) {
      /* malformed header — fall through to 401 */
    }
  }

  return new Response(
    expected
      ? "Authentication required."
      : "Locked: set the DASH_PASSWORD environment variable in Vercel, then redeploy.",
    {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="BHU Media Tracker", charset="UTF-8"',
        "Cache-Control": "no-store",
      },
    }
  );
}
