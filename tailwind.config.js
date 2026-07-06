/** Продакшен-сборка Tailwind для веб-интерфейса (без CDN).
 * Пересборка: скачать standalone CLI (tailwindcss-linux-x64 v3.4.x) и выполнить
 *   ./tailwindcss -c tailwind.config.js -i tailwind.input.css \
 *     -o src/tender_ingest/web/static/tailwind.css --minify
 */
module.exports = {
  content: ["./src/tender_ingest/web/templates/**/*.html"],
  theme: { extend: {} },
  plugins: [],
};
