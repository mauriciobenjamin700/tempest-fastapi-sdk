(function () {
  "use strict";
  var ATTRIBUTE = "data-confirm";
  function approved(element) {
    var message = element && element.getAttribute ? element.getAttribute(ATTRIBUTE) : null;
    if (!message) {
      return true;
    }
    return window.confirm(message);
  }
  function cancel(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
  document.addEventListener(
    "submit",
    function (event) {
      var submitter = event.submitter;
      if (submitter && submitter.hasAttribute && submitter.hasAttribute(ATTRIBUTE)) {
        if (!approved(submitter)) {
          cancel(event);
        }
        return;
      }
      if (!approved(event.target)) {
        cancel(event);
      }
    },
    true
  );
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target;
      var link = target && target.closest ? target.closest("a[" + ATTRIBUTE + "]") : null;
      if (link && !approved(link)) {
        cancel(event);
      }
    },
    true
  );
})();
