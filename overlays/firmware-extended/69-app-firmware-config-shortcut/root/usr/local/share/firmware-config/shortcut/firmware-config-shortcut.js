(function () {
  "use strict";

  var id = "extended-firmware-config-shortcut";
  var initialCollapseDelay = 8000;
  var hoverOutCollapseDelay = 800;

  if (document.getElementById(id)) {
    return;
  }

  function addShortcut() {
    if (document.getElementById(id) || !document.body) {
      return;
    }

    var style = document.createElement("style");
    style.textContent = `
#${id} {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 2147483647;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 34px;
  min-height: 40px;
  padding: 0 14px;
  border-radius: 999px;
  background: rgba(20, 24, 31, 0.92);
  color: #fff;
  font: 600 13px/1.2 sans-serif;
  text-decoration: none;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
  transition: opacity 160ms ease, background 160ms ease, transform 160ms ease;
}

#${id}:hover,
#${id}:focus {
  background: rgba(30, 36, 46, 0.98);
  opacity: 1;
  transform: translateY(-1px);
}

#${id}::before {
  display: none;
  content: "";
  width: 14px;
  height: 12px;
  background:
    linear-gradient(currentColor, currentColor) 0 0 / 100% 2px no-repeat,
    linear-gradient(currentColor, currentColor) 0 5px / 100% 2px no-repeat,
    linear-gradient(currentColor, currentColor) 0 10px / 100% 2px no-repeat;
}

#${id}.is-collapsed {
  padding-left: 10px;
  padding-right: 10px;
  opacity: 0.72;
  font-size: 0;
}

#${id}.is-collapsed::before {
  display: block;
}
`;
    document.head.appendChild(style);

    var link = document.createElement("a");
    var timer;

    link.id = id;
    link.href = "/firmware-config/";
    link.textContent = "Firmware Config";
    link.setAttribute("aria-label", "Open Extended Firmware Config");

    document.body.appendChild(link);

    function setCollapsed(collapsed) {
      clearTimeout(timer);
      link.classList.toggle("is-collapsed", collapsed);
    }

    function collapseAfter(delay) {
      clearTimeout(timer);
      timer = setTimeout(function () {
        setCollapsed(true);
      }, delay);
    }

    collapseAfter(initialCollapseDelay);
    link.addEventListener("pointerenter", function () { setCollapsed(false); });
    link.addEventListener("focus", function () { setCollapsed(false); });
    link.addEventListener("pointerleave", function () { collapseAfter(hoverOutCollapseDelay); });
    link.addEventListener("blur", function () { collapseAfter(hoverOutCollapseDelay); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addShortcut);
  } else {
    addShortcut();
  }
})();
