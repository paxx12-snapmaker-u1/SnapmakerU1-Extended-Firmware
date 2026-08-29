(function () {
  "use strict";

  var href = "/firmware-config/";
  var id = "extended-firmware-config-shortcut";

  if (document.getElementById(id)) {
    return;
  }

  function addShortcut() {
    if (document.getElementById(id)) {
      return;
    }

    var link = document.createElement("a");
    link.id = id;
    link.href = href;
    link.target = "_self";
    link.rel = "noopener";
    link.textContent = "Firmware Config";
    link.setAttribute("aria-label", "Open Extended Firmware Config");

    link.style.position = "fixed";
    link.style.right = "16px";
    link.style.bottom = "16px";
    link.style.zIndex = "2147483647";
    link.style.display = "inline-flex";
    link.style.alignItems = "center";
    link.style.justifyContent = "center";
    link.style.minHeight = "40px";
    link.style.padding = "0 14px";
    link.style.borderRadius = "999px";
    link.style.background = "rgba(20, 24, 31, 0.92)";
    link.style.color = "#fff";
    link.style.font = "600 13px/1.2 sans-serif";
    link.style.textDecoration = "none";
    link.style.boxShadow = "0 8px 24px rgba(0, 0, 0, 0.28)";
    link.style.backdropFilter = "blur(8px)";
    link.style.webkitBackdropFilter = "blur(8px)";

    link.addEventListener("mouseenter", function () {
      link.style.background = "rgba(30, 36, 46, 0.98)";
    });
    link.addEventListener("mouseleave", function () {
      link.style.background = "rgba(20, 24, 31, 0.92)";
    });

    document.body.appendChild(link);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addShortcut);
  } else {
    addShortcut();
  }
})();
