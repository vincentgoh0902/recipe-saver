// Small progressive enhancements for the home page. Everything works without it:
// the import form posts `source` either way and the server decides link vs text.

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("import-form");
  const source = document.getElementById("source");
  const msg = document.getElementById("import-msg");
  const search = document.querySelector("input.search");
  if (!form) return;

  const defaultMsg = msg.textContent;
  const say = (text, kind) => {
    msg.textContent = text;
    msg.className = "form-msg" + (kind ? " " + kind : "");
  };

  // TikTok link / Recipe text: only changes how the box looks and behaves.
  const setMode = (mode) => {
    const link = mode === "link";
    source.rows = link ? 1 : 7;
    source.placeholder = source.dataset[link ? "placeholderLink" : "placeholderText"];
    source.inputMode = link ? "url" : "text";
    source.spellcheck = !link;
    source.autocapitalize = link ? "off" : "sentences";
    form.classList.toggle("is-text", !link);
    document.querySelector(`input[name="import-mode"][value="${mode}"]`).checked = true;
  };
  document.querySelectorAll('input[name="import-mode"]').forEach((radio) =>
    radio.addEventListener("change", () => setMode(radio.value)));

  source.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !form.classList.contains("is-text")) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  // Pasting a whole recipe into the link box switches to the text layout.
  source.addEventListener("input", () => {
    if (!form.classList.contains("is-text") && source.value.includes("\n")) setMode("text");
  });

  form.addEventListener("htmx:beforeRequest", () => say("Starting the import…"));
  form.addEventListener("htmx:afterRequest", (e) => {
    if (e.detail.successful) {
      form.reset();
      say("Added. It's being imported below and will join your recipes when it's done.", "ok");
      setTimeout(() => msg.classList.contains("ok") && say(defaultMsg), 6000);
    } else {
      say("Couldn't start the import. Check your connection and try again.", "error");
    }
  });

  // Empty state: jump to the import box.
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-focus-import]")) {
      e.preventDefault();
      form.scrollIntoView({ behavior: "smooth", block: "center" });
      source.focus({ preventScroll: true });
    }
  });

  if (!search) return;

  // Tag chips and "Clear search" fill the search box and run the search.
  const syncChips = () => document.querySelectorAll(".chip[data-tag]").forEach((chip) =>
    chip.setAttribute("aria-pressed", String(chip.dataset.tag === search.value.trim().toLowerCase())));
  document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-tag]");
    if (!target) return;
    search.value = target.dataset.tag;
    syncChips();
    search.dispatchEvent(new Event("search"));
  });
  search.addEventListener("input", syncChips);
});
