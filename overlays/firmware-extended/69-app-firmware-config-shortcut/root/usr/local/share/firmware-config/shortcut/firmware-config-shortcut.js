(function () {
  "use strict";

  var id = "extended-firmware-config-shortcut";

  if (document.getElementById(id)) {
    return;
  }

  function addShortcut() {
    if (document.getElementById(id) || !document.body) {
      return;
    }

    var style = document.createElement("style");
    style.textContent = "#" + id + "{position:fixed;right:16px;bottom:16px;z-index:2147483647;display:inline-flex;align-items:center;justify-content:center;min-width:34px;min-height:40px;padding:0 14px;border-radius:999px;background:rgba(20,24,31,.92);color:#fff;font:600 13px/1.2 sans-serif;text-decoration:none;box-shadow:0 8px 24px rgba(0,0,0,.28);transition:opacity .16s ease,background .16s ease,transform .16s ease}#" + id + ":hover,#" + id + ":focus{background:rgba(30,36,46,.98);opacity:1;transform:translateY(-1px)}#" + id + ":before{display:none;content:'';width:14px;height:2px;border-radius:2px;background:currentColor;box-shadow:0 5px 0 currentColor,0 10px 0 currentColor}#" + id + ".is-collapsed{padding-left:10px;padding-right:10px;opacity:.72;font-size:0}#" + id + ".is-collapsed:before{display:block}";
    document.head.appendChild(style);

    var link = document.createElement("a");
    var timer;

    link.id = id;
    link.href = "/firmware-config/";
    link.textContent = "Firmware Config";
    link.setAttribute("aria-label", "Open Extended Firmware Config");

    document.body.appendChild(link);

    function show() {
      clearTimeout(timer);
      link.classList.remove("is-collapsed");
    }

    function hideSoon(delay) {
      clearTimeout(timer);
      timer = setTimeout(function () {
        link.classList.add("is-collapsed");
      }, delay);
    }

    hideSoon(8000);
    link.addEventListener("mouseenter", show);
    link.addEventListener("focus", show);
    link.addEventListener("mouseleave", function () { hideSoon(800); });
    link.addEventListener("blur", function () { hideSoon(800); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addShortcut);
  } else {
    addShortcut();
  }
})();
