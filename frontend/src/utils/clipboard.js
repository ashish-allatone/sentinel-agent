/**
 * Copy text to the clipboard, wherever the app happens to be served from.
 *
 * Three paths, tried in order, because no single one covers every context this
 * app runs in:
 *
 *  1. `navigator.clipboard.writeText` — the real API. It only exists in a
 *     secure context (HTTPS or localhost), and even then it rejects when the
 *     document is not focused or permission is denied. Tried whenever it is
 *     present rather than only when `isSecureContext` says so, since the check
 *     costs nothing and some browsers expose it more widely.
 *  2. A hidden textarea + `document.execCommand("copy")` — deprecated, but it
 *     is what works over plain HTTP, which is how this app is served from the
 *     API host.
 *  3. Selecting the text in a visible element, so the user can press Ctrl+C.
 *     The caller decides whether to say so.
 *
 * @param {string} text
 * @returns {Promise<boolean>} whether the text actually reached the clipboard
 */
export async function copyText(text) {
  const value = String(text == null ? "" : text);
  if (!value) return false;

  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (err) {
      // not fatal — the textarea path below still has a chance
    }
  }

  try {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    // off-screen and read-only, so the page does not scroll or flicker
    textarea.style.position = "fixed";
    textarea.style.top = "-9999px";
    textarea.style.left = "-9999px";
    textarea.setAttribute("readonly", "");
    document.body.appendChild(textarea);

    const selection = document.getSelection();
    const previous = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;

    textarea.select();
    textarea.setSelectionRange(0, value.length);
    const ok = document.execCommand("copy");

    document.body.removeChild(textarea);
    // put the user's own selection back, so copying does not steal it
    if (previous && selection) {
      selection.removeAllRanges();
      selection.addRange(previous);
    }
    return ok;
  } catch (err) {
    return false;
  }
}

export default copyText;
