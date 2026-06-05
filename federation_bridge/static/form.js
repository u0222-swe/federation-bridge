// SPDX-License-Identifier: AGPL-3.0-or-later
// Progressive disclosure for the bridge form. Containers carrying a data-*
// dependency attribute are disabled (and dimmed) until their controlling input
// is active. Disabling — rather than just hiding — means inactive controls are
// not submitted, so the persisted config stays in sync with what is shown.
//
//   data-enabled-by="name"          active when checkbox `name` is checked
//   data-enabled-by-filled="name"   active when input `name` is non-empty / non-"0"
//   data-show-when="name=v1,v2"     active when control `name`'s value is listed
(function () {
  "use strict";

  function controlValue(form, name) {
    var el = form.elements[name];
    if (!el) return null;
    if (el.type === "checkbox") return el.checked;
    return el.value;
  }

  function isActive(form, group) {
    var ds = group.dataset;
    if (ds.enabledBy) {
      return !!controlValue(form, ds.enabledBy);
    }
    if (ds.enabledByFilled) {
      var v = controlValue(form, ds.enabledByFilled);
      return v != null && v !== "" && v !== "0";
    }
    if (ds.showWhen) {
      var parts = ds.showWhen.split("=");
      var allowed = (parts[1] || "").split(",");
      return allowed.indexOf(String(controlValue(form, parts[0]))) !== -1;
    }
    return true;
  }

  function apply(form) {
    var groups = form.querySelectorAll(
      "[data-enabled-by], [data-enabled-by-filled], [data-show-when]"
    );
    groups.forEach(function (group) {
      var active = isActive(form, group);
      group.classList.toggle("group-disabled", !active);
      group.querySelectorAll("input, select, textarea, button").forEach(function (ctrl) {
        ctrl.disabled = !active;
      });
    });
  }

  function init(form) {
    apply(form);
    form.addEventListener("change", function () { apply(form); });
    form.addEventListener("input", function () { apply(form); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form.card").forEach(init);
  });
})();
