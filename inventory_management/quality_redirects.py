from flask import redirect, request


def register_quality_redirects(app):
    """Recover relative redirects emitted from nested quality POST routes.

    A redirect target such as ``quality?audit=1`` returned from
    ``/quality/whitelist`` is resolved by the browser as
    ``/quality/quality?audit=1``. Keep this compatibility route so all
    quality actions return to the canonical mounted checker URL.
    """

    @app.route("/quality/quality")
    def quality_canonical_redirect():
        script_root = request.environ.get("SCRIPT_NAME", "") or ""
        target = f"{script_root}/quality"
        if request.query_string:
            target += "?" + request.query_string.decode("utf-8", errors="ignore")
        return redirect(target)
