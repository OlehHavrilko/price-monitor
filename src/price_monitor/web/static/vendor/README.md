# Vendored browser libraries

No CDN requests are made by the dashboard. These are unmodified upstream distributions:

- HTMX 2.0.4: https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js (0BSD, HTMX-LICENSE.txt).
- Chart.js 4.4.8: https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.js (MIT, Chart-LICENSE.md).

Chart.js coordinates use JavaScript numbers only for drawing; API prices and tooltip/table values remain strings.
