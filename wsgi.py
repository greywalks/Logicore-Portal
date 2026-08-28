"""Production/local WSGI composition for the Logicore Portal.

Keeps the existing portal app untouched while mounting Inventory Management as
its own Flask application. Render should start `wsgi:application`.
"""
import re
import threading
import webbrowser
from pathlib import Path

from jinja2 import BaseLoader
from werkzeug.middleware.dispatcher import DispatcherMiddleware

import portal_auth
import nonconforming
from runtime_storage import resolve_runtime_db

# Resolve persistent runtime databases before importing app.py. app.py calls
# portal_auth.init_db() and nonconforming.init_db() during import, so these
# module-level paths must be set first.
portal_auth.DB_PATH = resolve_runtime_db(
    "portal_auth.db",
    "PORTAL_AUTH_DB_PATH",
    Path(__file__).parent / "portal_auth.db",
)
nonconforming.DB_PATH = resolve_runtime_db(
    "nonconforming.db",
    "NONCONFORMING_DB_PATH",
    Path(__file__).parent / "nonconforming.db",
)

from app import app as portal_app
from inventory_management.app import app as inventory_app, init_db as _inventory_init_db, db_connect as _inventory_db_connect, clean as _inventory_clean, DATA_DIR as _inventory_data_dir
from inventory_management.shipping_history import register_shipping_history
from inventory_management.promethean_quality_v3 import register_quality_checker
from inventory_management.excel_exports import register_excel_exports
from inventory_management.quality_redirects import register_quality_redirects


class _PortalInventoryTemplateLoader(BaseLoader):
    """Small overlay that replaces the legacy TBD 2 portal affordance."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    def get_source(self, environment, template):
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "index.html":
            nav_pattern = re.compile(
                r"\{% if can_tbd2 %\}.*?id=\"portal-nav-tbd2\".*?\{% endif %\}",
                re.S,
            )
            inventory_nav = """{% if can_tbd2 %}
    <a class=\"side-link text-steel\" href=\"/inventory-management/\" id=\"portal-nav-inventory-management\">
      <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M4 6h16v12H4z\"/><path d=\"M8 10h8M8 14h5\"/></svg>
      <span>Inventory Management</span>
    </a>
    {% endif %}"""
            source = nav_pattern.sub(inventory_nav, source, count=1)
            source = source.replace("PORTAL TAB: TBD 2 (placeholder)", "PORTAL APP: INVENTORY MANAGEMENT")
            source = source.replace(">TBD 2<", ">Inventory Management<")
            source = source.replace(
                "Not built yet — this tab is reserved for a future module.",
                "Inventory Management opens in its own workspace. Use the sidebar link to search or import inventory history.",
            )
        return source, filename, uptodate

    def list_templates(self):
        return self.wrapped.list_templates()


portal_auth.SECTIONS["tbd2"]["label"] = "Inventory Management"
portal_app.jinja_loader = _PortalInventoryTemplateLoader(portal_app.jinja_loader)
inventory_app.secret_key = portal_app.secret_key
_inventory_init_db()
register_shipping_history(inventory_app, _inventory_db_connect, _inventory_clean)
register_quality_checker(inventory_app, _inventory_db_connect, _inventory_clean, _inventory_data_dir)
register_excel_exports(inventory_app, _inventory_db_connect, _inventory_clean, _inventory_data_dir)
register_quality_redirects(inventory_app)

portal_app.wsgi_app = DispatcherMiddleware(
    portal_app.wsgi_app,
    {"/inventory-management": inventory_app.wsgi_app},
)

application = portal_app
app = application


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\n  Logicore Portal running → {url}\n  Inventory Management mounted at /inventory-management/\n")
    application.run(debug=False, port=5000)
