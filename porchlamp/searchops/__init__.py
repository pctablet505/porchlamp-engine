"""Search operations by API after a publish: IndexNow, Google Search Console and Bing Webmaster.

Run as ``python -m porchlamp.searchops <command>`` (:mod:`porchlamp.searchops.cli`). The modules
behind the CLI are pure (:mod:`~porchlamp.searchops.pages`, :mod:`~porchlamp.searchops.plan`,
:mod:`~porchlamp.searchops.report` and the request builders in :mod:`~porchlamp.searchops.gsc` and
:mod:`~porchlamp.searchops.bing`); the network is one thin layer,
:mod:`~porchlamp.searchops.transport`. Credentials are files whose paths two environment variables
hold (:mod:`~porchlamp.searchops.credentials`); no secret is ever printed, logged or raised.
"""
