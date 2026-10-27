

(function () {
  "use strict";

  var KINDS = [
    { value: "musician", label: "Musician" },
    { value: "film", label: "Film" },
    { value: "cuisine", label: "Cuisine" },
    { value: "brand", label: "Brand" },
    { value: "other", label: "Anything else" }
  ];

  var KIND_LABELS = {
    attraction: "Place to visit",
    restaurant: "Restaurant",
    cafe: "Cafe",
    music: "Music spot",
    bar: "Bar",
    market: "Market",
    place: "Place"
  };

  var EXAMPLE_TASTES = [
    { kind: "musician", label: "Taylor Swift" },
    { kind: "film", label: "Blade Runner" },
    { kind: "cuisine", label: "ramen" }
  ];

  var ROW_PLACEHOLDERS = [
    "Taylor Swift",
    "Blade Runner",
    "ramen",
    "A book you love",
    "A place you miss"
  ];

  var MIN_TASTES = 3;
  var MAX_TASTES = 5;

  
  var stepTastes = document.getElementById("step-tastes");
  var stepCities = document.getElementById("step-cities");
  var stepPlan = document.getElementById("step-plan");
  var loadingEl = document.getElementById("loading");
  var loadingText = document.getElementById("loading-text");
  var errorBox = document.getElementById("error-box");
  var errorText = document.getElementById("error-text");
  var retryBtn = document.getElementById("retry-btn");
  var progressText = document.getElementById("progress-text");
  var progressSteps = document.querySelectorAll(".progress-step");

  var form = document.getElementById("taste-form");
  var tasteRowsEl = document.getElementById("taste-rows");
  var addTasteBtn = document.getElementById("add-taste-btn");
  var exampleBtn = document.getElementById("example-btn");
  var formError = document.getElementById("form-error");
  var findBtn = document.getElementById("find-btn");

  var citiesSub = document.getElementById("cities-sub");
  var citiesNote = document.getElementById("cities-note");
  var cityList = document.getElementById("city-list");

  var planTitle = document.getElementById("plan-title");
  var planSub = document.getElementById("plan-sub");
  var planNote = document.getElementById("plan-note");
  var matchesBlock = document.getElementById("matches-block");
  var matchesList = document.getElementById("matches-list");
  var weeksList = document.getElementById("weeks-list");

  
  var state = {
    tastes: [],
    cities: [],
    selectedCity: null,
    lastAction: null
  };

  
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }

  function hideAllSteps() {
    hide(stepTastes);
    hide(stepCities);
    hide(stepPlan);
    hide(loadingEl);
    hide(errorBox);
  }

  function setProgress(step) {
    progressText.textContent = step === 1 ? "Step 1 of 2" : "Step 2 of 2";
    progressSteps.forEach(function (el) {
      var n = Number(el.getAttribute("data-step"));
      el.classList.toggle("is-active", n === step);
      el.classList.toggle("is-done", n < step);
    });
  }

  function showStep(name) {
    hideAllSteps();
    if (name === "tastes") { show(stepTastes); setProgress(1); }
    if (name === "cities") { show(stepCities); setProgress(2); }
    if (name === "plan") { show(stepPlan); setProgress(2); }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showLoading(text) {
    hideAllSteps();
    loadingText.textContent = text;
    show(loadingEl);
  }

  function showError(message, action) {
    hideAllSteps();
    errorText.textContent = message;
    state.lastAction = action || state.lastAction;
    show(errorBox);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setNote(el, note) {
    if (note) {
      el.textContent = note;
      show(el);
    } else {
      hide(el);
    }
  }

  function tasteSummary(tastes) {
    return tastes.map(function (t) { return t.label; }).join(", ");
  }

  
  function rowCount() {
    return tasteRowsEl.querySelectorAll(".taste-row").length;
  }

  function addTasteRow(kind, label, placeholder) {
    var row = document.createElement("div");
    row.className = "taste-row";

    var select = document.createElement("select");
    select.setAttribute("aria-label", "Taste type");
    KINDS.forEach(function (k) {
      var opt = document.createElement("option");
      opt.value = k.value;
      opt.textContent = k.label;
      if (k.value === kind) opt.selected = true;
      select.appendChild(opt);
    });

    var input = document.createElement("input");
    input.type = "text";
    input.placeholder = placeholder || "What do you love?";
    input.maxLength = 80;
    input.autocomplete = "off";
    if (label) input.value = label;

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "row-remove";
    remove.title = "Remove this taste";
    remove.setAttribute("aria-label", "Remove this taste");
    remove.textContent = "\u00d7";
    remove.addEventListener("click", function () {
      if (rowCount() > MIN_TASTES) {
        row.remove();
        refreshRowControls();
      }
    });

    row.appendChild(select);
    row.appendChild(input);
    row.appendChild(remove);
    tasteRowsEl.appendChild(row);
    refreshRowControls();
  }

  function refreshRowControls() {
    var count = rowCount();
    addTasteBtn.disabled = count >= MAX_TASTES;
    addTasteBtn.textContent = count >= MAX_TASTES
      ? "That is 5, plenty to go on"
      : "+ Add another";
    tasteRowsEl.querySelectorAll(".row-remove").forEach(function (btn) {
      btn.style.visibility = count > MIN_TASTES ? "visible" : "hidden";
    });
  }

  function readTastes() {
    var tastes = [];
    tasteRowsEl.querySelectorAll(".taste-row").forEach(function (row) {
      var kind = row.querySelector("select").value;
      var label = row.querySelector("input").value.trim();
      if (label) tastes.push({ kind: kind, label: label });
    });
    return tastes;
  }

  function setFormError(message) {
    if (message) {
      formError.textContent = message;
      show(formError);
    } else {
      hide(formError);
    }
  }

  
  function postJson(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok || !data || data.ok === false) {
          var msg = (data && data.error) || "The server did not answer. Please try again.";
          throw new Error(msg);
        }
        return data;
      });
    }).catch(function (err) {
      if (err instanceof TypeError) {
        throw new Error("Could not reach the server. Check your connection and try again.");
      }
      throw err;
    });
  }

  
  function reasonLine(reasons) {
    if (!reasons || !reasons.length) return "";
    var parts = reasons.map(function (r) {
      return esc(r.label) + " (" + Math.round(r.score * 100) + "%)";
    });
    if (parts.length === 1) return "Because you love " + parts[0];
    var last = parts.pop();
    return "Because you love " + parts.join(", ") + " and " + last;
  }

  function badgeClass(level, label) {
    if (level === "strong") return "badge-strong";
    if (level === "good") return "badge-good";
    if (level === "loose") return "badge-loose";
    if (label === "Strong fit") return "badge-strong";
    if (label === "Good fit") return "badge-good";
    return "badge-loose";
  }

  function renderCities(data) {
    state.cities = data.cities || [];
    citiesSub.textContent = "Ranked for your love of " + tasteSummary(state.tastes) + ".";
    setNote(citiesNote, data.note);
    cityList.innerHTML = "";

    if (!state.cities.length) {
      showError("Qloo did not return any cities for that taste. Try different favourites.", "cities");
      return;
    }

    state.cities.slice(0, 5).forEach(function (city, i) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "card city-card";
      card.setAttribute("aria-label", city.name + ", " + city.country + ", " + city.fitScore + " percent fit. Start my first month.");

      var countryLine = city.country || "";
      if (city.region && city.region !== city.country) {
        countryLine += " \u00b7 " + city.region;
      }
      var tags = (city.tags || []).slice(0, 4);
      var conf = city.confidence || {};
      var reason = reasonLine(city.reasons);

      card.innerHTML =
        '<span class="city-rank" aria-hidden="true">' + (i + 1) + "</span>" +
        '<span class="city-main">' +
          '<span class="city-name-row">' +
            '<span class="city-name">' + esc(city.name) + "</span>" +
            '<span class="city-country">' + esc(countryLine) + "</span>" +
          "</span>" +
          (reason ? '<span class="city-reason">' + reason + "</span>" : "") +
          (tags.length
            ? '<span class="city-tags">' + tags.map(function (t) {
                return '<span class="tag">' + esc(t) + "</span>";
              }).join("") + "</span>"
            : "") +
        "</span>" +
        '<span class="city-side">' +
          '<span class="fit-score">' + esc(city.fitScore) + "<small>% fit</small></span>" +
          '<span class="fit-bar" aria-hidden="true"><span class="fit-bar-fill" style="width:' +
            Math.max(0, Math.min(100, Number(city.fitScore) || 0)) + '%"></span></span>' +
          (conf.label ? '<span class="badge ' + badgeClass(conf.level, conf.label) + '">' + esc(conf.label) + "</span>" : "") +
          '<span class="city-go">Start my first month &rarr;</span>' +
        "</span>";

      card.addEventListener("click", function () {
        loadPlan(city);
      });
      cityList.appendChild(card);
    });

    showStep("cities");
  }

  function loadCities() {
    var tastes = readTastes();
    if (tastes.length < MIN_TASTES) {
      setFormError("Add at least 3 things you love, so Qloo has taste to rank with.");
      return;
    }
    if (tastes.length > MAX_TASTES) {
      setFormError("Keep it to 5 things at most.");
      return;
    }
    state.tastes = tastes;
    setFormError("");
    findBtn.disabled = true;
    findBtn.textContent = "Finding your city\u2026";
    showLoading("Asking Qloo where you fit\u2026");
    postJson("/api/cities", { tastes: tastes })
      .then(function (data) {
        findBtn.disabled = false;
        findBtn.textContent = "Find my city";
        renderCities(data);
      })
      .catch(function (err) {
        findBtn.disabled = false;
        findBtn.textContent = "Find my city";
        showError(err.message, "cities");
      });
  }

  
  function kindLabel(kind) {
    return KIND_LABELS[kind] || (kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "Place");
  }

  function renderStop(stop) {
    var card = document.createElement("article");
    card.className = "card stop-card";

    var imgHtml = "";
    if (stop.image) {
      imgHtml = '<img class="stop-img" src="' + esc(stop.image) + '" alt="' + esc(stop.name) +
        '" loading="lazy" onerror="this.remove()">';
    }

    var metaBits = [];
    if (stop.rating != null) {
      metaBits.push('<span class="rating">\u2605 ' + esc(stop.rating) + "</span>");
    }
    if (stop.priceLevel != null) {
      metaBits.push('<span class="price">' + "$".repeat(Math.max(1, Math.min(4, stop.priceLevel))) + "</span>");
    }
    if (stop.affinity != null) {
      metaBits.push("<span>Qloo match " + Math.round(stop.affinity * 100) + "%</span>");
    }

    var tasteChips = "";
    if (stop.tastes && stop.tastes.length) {
      tasteChips = '<span class="stop-tastes">' + stop.tastes.map(function (t) {
        return '<span class="tag">' + esc(t) + "</span>";
      }).join("") + "</span>";
    }

    card.innerHTML = imgHtml +
      '<div class="stop-body">' +
        '<span class="stop-kind">' + esc(kindLabel(stop.kind)) + "</span>" +
        '<span class="stop-name">' + esc(stop.name) + "</span>" +
        (stop.why ? '<p class="stop-why">' + esc(stop.why) + "</p>" : "") +
        tasteChips +
        (stop.address ? '<p class="stop-address">' + esc(stop.address) + "</p>" : "") +
        (metaBits.length ? '<div class="stop-meta">' + metaBits.join("") + "</div>" : "") +
      "</div>";
    return card;
  }

  function renderPlan(data) {
    var city = state.selectedCity;
    planTitle.textContent = "Your first month in " + city.name;
    var sub = "A week by week plan built from your taste, for " + city.name +
      (city.country ? ", " + city.country : "") + ".";
    if (data.meta && data.meta.localityMatched && data.meta.localityMatched !== city.name) {
      sub += " Matched to " + data.meta.localityMatched + " on the map.";
    }
    planSub.textContent = sub;
    setNote(planNote, data.note);

    var matches = data.matches || [];
    if (matches.length) {
      matchesList.innerHTML = "";
      matches.forEach(function (m) {
        var places = (m.places || []).join(", ");
        var li = document.createElement("li");
        li.innerHTML =
          '<span class="matches-taste">Your love of ' + esc(m.taste) + "</span>" +
          '<span class="matches-arrow" aria-hidden="true">&rarr;</span>' +
          "<span>" + esc(places) + "</span>";
        matchesList.appendChild(li);
      });
      show(matchesBlock);
    } else {
      hide(matchesBlock);
    }

    weeksList.innerHTML = "";
    var weeks = (data.plan && data.plan.weeks) || [];
    weeks.forEach(function (week) {
      var section = document.createElement("section");
      section.className = "week";

      var head = document.createElement("div");
      head.className = "week-head";
      head.innerHTML =
        '<p class="week-kicker">Week ' + esc(week.week) + "</p>" +
        '<h3 class="week-title">' + esc(week.title) + "</h3>" +
        (week.theme ? '<p class="week-theme">' + esc(week.theme) + "</p>" : "");
      section.appendChild(head);

      var grid = document.createElement("div");
      grid.className = "stop-grid";
      (week.stops || []).forEach(function (stop) {
        grid.appendChild(renderStop(stop));
      });
      section.appendChild(grid);
      weeksList.appendChild(section);
    });

    showStep("plan");
  }

  function loadPlan(city) {
    state.selectedCity = city;
    showLoading("Mapping your first month in " + city.name + "\u2026");
    postJson("/api/first-month", { destination: city.name, tastes: state.tastes })
      .then(function (data) {
        renderPlan(data);
      })
      .catch(function (err) {
        showError(err.message, "plan");
      });
  }

  
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    loadCities();
  });

  addTasteBtn.addEventListener("click", function () {
    if (rowCount() >= MAX_TASTES) return;
    addTasteRow("other", "", ROW_PLACEHOLDERS[rowCount()] || "What do you love?");
    var rows = tasteRowsEl.querySelectorAll(".taste-row");
    rows[rows.length - 1].querySelector("input").focus();
  });

  exampleBtn.addEventListener("click", function () {
    tasteRowsEl.innerHTML = "";
    EXAMPLE_TASTES.forEach(function (t, i) {
      addTasteRow(t.kind, t.label, ROW_PLACEHOLDERS[i]);
    });
    setFormError("");
  });

  retryBtn.addEventListener("click", function () {
    if (state.lastAction === "plan" && state.selectedCity) {
      loadPlan(state.selectedCity);
    } else if (state.tastes.length >= MIN_TASTES) {
      showLoading("Asking Qloo where you fit\u2026");
      postJson("/api/cities", { tastes: state.tastes })
        .then(renderCities)
        .catch(function (err) { showError(err.message, "cities"); });
    } else {
      showStep("tastes");
    }
  });

  document.querySelectorAll("[data-back]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      showStep(btn.getAttribute("data-back"));
    });
  });

  document.getElementById("restart-btn").addEventListener("click", function () {
    showStep("tastes");
  });

  document.getElementById("wordmark").addEventListener("click", function (event) {
    event.preventDefault();
    showStep("tastes");
  });

  
  addTasteRow("musician", "", ROW_PLACEHOLDERS[0]);
  addTasteRow("film", "", ROW_PLACEHOLDERS[1]);
  addTasteRow("cuisine", "", ROW_PLACEHOLDERS[2]);
})();
