// Small usability helpers. No inline scripts are used (Content-Security-Policy: script-src 'self').
(function () {
  "use strict";

  function parseDecimal(value) {
    if (!value) return NaN;
    return parseFloat(String(value).replace(/\s/g, "").replace(",", "."));
  }

  function formatMoney(value) {
    return value.toLocaleString("bg-BG", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  }

  // When a vehicle is chosen, fill in its current mileage and show the unit (л / kWh).
  var vehicleSelect = document.getElementById("vehicle_id");
  var mileageInput = document.getElementById("mileage") || document.getElementById("start_mileage");
  if (vehicleSelect && vehicleSelect.form && vehicleSelect.form.method === "post") {
    vehicleSelect.addEventListener("change", function () {
      var option = vehicleSelect.options[vehicleSelect.selectedIndex];
      if (mileageInput && option && option.dataset.mileage) {
        mileageInput.value = option.dataset.mileage;
      }
    });
  }

  // Live total for refuels: quantity × unit price.
  var form = document.querySelector("form[data-live-total]");
  if (form) {
    var names = form.dataset.liveTotal.split(",");
    var quantity = document.getElementById(names[0]);
    var price = document.getElementById(names[1]);
    var output = document.getElementById("live-total");
    var update = function () {
      var total = parseDecimal(quantity.value) * parseDecimal(price.value);
      if (isFinite(total) && total > 0) {
        output.textContent = "Обща сума: " + formatMoney(Math.round(total * 100) / 100);
        output.classList.remove("d-none");
      } else {
        output.classList.add("d-none");
      }
    };
    quantity.addEventListener("input", update);
    price.addEventListener("input", update);
    update();
  }

  // Insurance: suggest an end date one year after the start date.
  var startDate = document.getElementById("start_date");
  var endDate = document.getElementById("end_date");
  if (startDate && endDate) {
    startDate.addEventListener("change", function () {
      if (!startDate.value || endDate.dataset.touched) return;
      var start = new Date(startDate.value + "T00:00:00");
      var end = new Date(start);
      end.setFullYear(end.getFullYear() + 1);
      end.setDate(end.getDate() - 1);
      var pad = function (n) { return String(n).padStart(2, "0"); };
      endDate.value = end.getFullYear() + "-" + pad(end.getMonth() + 1) + "-" + pad(end.getDate());
    });
    endDate.addEventListener("input", function () { endDate.dataset.touched = "1"; });
  }
})();
