import { copyFile, mkdir } from "node:fs/promises";

await mkdir(new URL("../static/vendor/", import.meta.url), { recursive: true });
await copyFile(
  new URL("../node_modules/chart.js/dist/chart.umd.js", import.meta.url),
  new URL("../static/vendor/chart.umd.js", import.meta.url),
);
await copyFile(
  new URL("../node_modules/chart.js/dist/chart.umd.js.map", import.meta.url),
  new URL("../static/vendor/chart.umd.js.map", import.meta.url),
);
