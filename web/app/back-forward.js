// back-forward.js — the one script of a ranked project's static page (porchlamp/serve/project_pages.py).
//
// The page's address is also the address of the project's side panel in the app (routes.js "project"), and the
// entries the app adds to the tab's history belong to the document at that address. A reload of an open panel,
// or its "Full page" link, puts this page in that document's place, so Back and Forward between the app's
// entries arrive here as `popstate`: the address changes, and this page cannot draw it. Load the address instead,
// so the app draws it. A Back to another page is a page load of its own and never reaches this handler.
addEventListener("popstate", () => location.reload());
