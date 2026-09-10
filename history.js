(function () {
  var dataEl = document.getElementById("gl-history");
  var stage = document.getElementById("history-stage");
  if (!dataEl || !stage) return;

  var data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (err) {
    return;
  }

  var overlay = document.getElementById("history-overlay");
  var chart = document.getElementById("history-chart");
  var yearEl = document.getElementById("history-year");
  var monthEl = document.getElementById("history-month");
  var eventEl = document.getElementById("history-event");
  var playFab = document.getElementById("history-play-fab");
  var toggleBtn = document.getElementById("history-toggle");
  var scrub = document.getElementById("history-scrub");
  var readout = document.getElementById("history-readout");
  var kpis = document.getElementById("history-kpis");
  var bg = document.getElementById("history-bg");
  if (!overlay || !chart || !yearEl || !toggleBtn || !scrub || !kpis) return;

  var DURATION = (data.duration_s || 30) * 1000;
  var START = data.start_year || 1918;
  var nMonths = data.n_months || 0;
  var lakes = data.lakes || {};
  var basins = data.basins || [];
  var events = data.events || [];
  var order = ["superior", "michigan_huron", "st_clair", "erie", "ontario"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  var progress = 0;
  var playing = false;
  var origin = 0;
  var raf = 0;
  var caption = { text: "", until: -1 };

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function atIndex(arr, t) {
    if (!arr || !arr.length) return null;
    var i0 = Math.max(0, Math.min(arr.length - 1, Math.floor(t)));
    var i1 = Math.min(arr.length - 1, i0 + 1);
    var a = arr[i0];
    var b = arr[i1];
    if (a == null && b == null) return null;
    if (a == null) return b;
    if (b == null) return a;
    return lerp(a, b, t - i0);
  }

  function monthT(p) {
    if (nMonths <= 1) return 0;
    return p * (nMonths - 1);
  }

  function yearMonth(t) {
    var idx = Math.max(0, Math.min(nMonths - 1, t));
    var year = START + Math.floor(idx / 12);
    var month = Math.floor(idx % 12);
    return { year: year, month: month };
  }

  function eventForYear(year, p) {
    var playMs = p * DURATION;
    if (caption.text && playMs < caption.until) return caption.text;
    var hit = null;
    for (var i = 0; i < events.length; i++) {
      if (events[i].year === year) hit = events[i].text;
    }
    if (hit) {
      caption = { text: hit, until: playMs + 2200 };
      return hit;
    }
    if (caption.text && playMs < caption.until + 400) return caption.text;
    caption = { text: "", until: -1 };
    return "";
  }

  function sizeCanvas(cnv, cssW, cssH) {
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var bw = Math.max(1, Math.round(cssW * dpr));
    var bh = Math.max(1, Math.round(cssH * dpr));
    if (cnv.width !== bw) cnv.width = bw;
    if (cnv.height !== bh) cnv.height = bh;
    var ctx = cnv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  function drawProfile(p) {
    var w = Math.min(980, stage.clientWidth);
    var h = stage.clientHeight;
    if (w < 40 || h < 40) return;
    var ctx = sizeCanvas(overlay, w, h);
    ctx.clearRect(0, 0, w, h);
    var t = monthT(p);
    var ym = yearMonth(t);

    basins.forEach(function (basin) {
      var lake = lakes[basin.key];
      if (!lake) return;
      var level = atIndex(lake.smooth || lake.monthly, t);
      if (level == null) return;
      var lwd = lake.lwd;
      var mean = lake.mean == null ? lwd : lake.mean;
      var dy = (level - lwd) * 0.145;
      var ySurf = (basin.y_lwd - dy) * h;
      var yLwd = basin.y_lwd * h;
      var poly = basin.poly || [];
      if (poly.length < 3) return;

      ctx.save();
      ctx.beginPath();
      poly.forEach(function (pt, i) {
        var x = pt[0] * w;
        var y = pt[1] * h;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.clip();

      var vsMean = level - mean;
      var hi = Math.max(0, Math.min(1, 0.5 + vsMean / 1.2));
      var r = Math.round(lerp(196, 20, hi));
      var g = Math.round(lerp(168, 70, hi));
      var b = Math.round(lerp(120, 130, hi));
      ctx.fillStyle = "rgba(" + r + "," + g + "," + b + ",0.48)";
      var ys = Math.max(4, Math.min(h - 8, ySurf));
      ctx.fillRect(0, ys, w, h);

      if (ys < yLwd) {
        ctx.fillStyle = "rgba(90, 180, 210, 0.28)";
        ctx.fillRect(0, ys, w, yLwd - ys);
      } else if (ys > yLwd) {
        ctx.fillStyle = "rgba(196, 168, 120, 0.38)";
        ctx.fillRect(0, yLwd, w, ys - yLwd);
      }

      ctx.strokeStyle = "rgba(255,255,255,0.95)";
      ctx.lineWidth = 2.4;
      ctx.beginPath();
      ctx.moveTo(0, ys);
      ctx.lineTo(w, ys);
      ctx.stroke();
      ctx.restore();

      var lab = basin.label || [poly[0][0], basin.y_lwd];
      var vs = (level - lwd) * 100;
      var yoy = null;
      var prev = atIndex(lake.smooth || lake.monthly, t - 12);
      if (prev != null) yoy = (level - prev) * 100;
      var arrow = yoy == null || Math.abs(yoy) < 1.5 ? "–" : yoy > 0 ? "▲" : "▼";
      var ax = lab[0] * w;
      var ay = lab[1] * h;
      ctx.fillStyle = "rgba(16, 40, 52, 0.82)";
      roundRect(ctx, ax - 72, ay - 28, 144, 52, 8);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 12px Arial, Helvetica, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(lake.label, ax, ay - 12);
      ctx.font = "bold 15px Arial, Helvetica, sans-serif";
      ctx.fillText(level.toFixed(2) + " m", ax, ay + 6);
      ctx.font = "11px Arial, Helvetica, sans-serif";
      ctx.fillStyle = vs >= 0 ? "#7dcea0" : "#e07a5f";
      ctx.fillText((vs >= 0 ? "+" : "") + vs.toFixed(0) + " cm vs LWD  " + arrow, ax, ay + 20);
    });

    yearEl.textContent = String(ym.year);
    if (monthEl) monthEl.textContent = MONTHS[ym.month];
    var ev = eventForYear(ym.year, p);
    if (eventEl) eventEl.textContent = ev || "Annual cycle removed — 12-month mean, IGLD 1985";
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawChart(p) {
    var wrap = chart.parentElement;
    var w = Math.min(980, (wrap && wrap.clientWidth) || 900);
    var h = 168;
    var ctx = sizeCanvas(chart, w, h);
    ctx.clearRect(0, 0, w, h);
    var pad = { l: 44, r: 16, t: 12, b: 28 };
    var iw = w - pad.l - pad.r;
    var ih = h - pad.t - pad.b;
    var t = monthT(p);
    var years = data.years || [];
    var nY = years.length || 1;

    ctx.fillStyle = "#f4f8f9";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "#d7e4e8";
    ctx.strokeRect(0.5, 0.5, w - 1, h - 1);

    var minV = 0;
    var maxV = 0;
    order.forEach(function (key) {
      var lake = lakes[key];
      if (!lake) return;
      (lake.annual || []).forEach(function (v) {
        if (v == null || lake.mean == null) return;
        var cm = (v - lake.mean) * 100;
        if (cm < minV) minV = cm;
        if (cm > maxV) maxV = cm;
      });
    });
    var span = Math.max(20, maxV - minV);
    minV -= span * 0.08;
    maxV += span * 0.08;

    function xAtYear(y) {
      return pad.l + ((y - START) / Math.max(1, nY - 1)) * iw;
    }
    function yAt(cm) {
      return pad.t + ((maxV - cm) / (maxV - minV)) * ih;
    }

    ctx.strokeStyle = "#d5dde3";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l, yAt(0));
    ctx.lineTo(pad.l + iw, yAt(0));
    ctx.stroke();
    ctx.fillStyle = "#5a7a86";
    ctx.font = "10px Arial, Helvetica, sans-serif";
    ctx.textAlign = "right";
    ctx.fillText("+cm vs mean", pad.l - 6, pad.t + 8);
    ctx.fillText("0", pad.l - 6, yAt(0) + 3);

    var playYear = START + t / 12;
    order.forEach(function (key) {
      var lake = lakes[key];
      if (!lake || !lake.annual) return;
      ctx.strokeStyle = lake.color;
      ctx.lineWidth = 1.1;
      ctx.globalAlpha = 0.28;
      ctx.beginPath();
      var started = false;
      lake.annual.forEach(function (v, i) {
        if (v == null || lake.mean == null) {
          started = false;
          return;
        }
        var x = xAtYear(START + i);
        var y = yAt((v - lake.mean) * 100);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 2.1;
      ctx.beginPath();
      started = false;
      lake.annual.forEach(function (v, i) {
        if (v == null || lake.mean == null) {
          started = false;
          return;
        }
        if (START + i > playYear + 0.02) return;
        var x = xAtYear(START + i);
        var y = yAt((v - lake.mean) * 100);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });

    var px = xAtYear(Math.min(START + nY - 1, playYear));
    ctx.strokeStyle = "#1a3a4a";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(px, pad.t);
    ctx.lineTo(px, pad.t + ih);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "#5a7a86";
    ctx.font = "10px Arial, Helvetica, sans-serif";
    ctx.textAlign = "center";
    [1918, 1940, 1964, 1986, 2000, 2013, 2020].forEach(function (y) {
      if (y < START || y > START + nY - 1) return;
      ctx.fillText(String(y), xAtYear(y), h - 8);
    });
  }

  function renderKpis(p) {
    var t = monthT(p);
    var html = "";
    order.forEach(function (key) {
      var lake = lakes[key];
      if (!lake) return;
      var level = atIndex(lake.smooth || lake.monthly, t);
      var prev = atIndex(lake.smooth || lake.monthly, t - 12);
      var vs = level == null ? null : (level - lake.lwd) * 100;
      var yoy = level == null || prev == null ? null : (level - prev) * 100;
      var cls = "flat";
      var mark = "–";
      if (yoy != null && Math.abs(yoy) >= 1.5) {
        cls = yoy > 0 ? "up" : "down";
        mark = (yoy > 0 ? "▲" : "▼") + " " + (yoy > 0 ? "+" : "") + yoy.toFixed(1) + " cm/yr";
      } else if (yoy != null) {
        mark = "steady " + (yoy >= 0 ? "+" : "") + yoy.toFixed(1) + " cm/yr";
      }
      html +=
        '<div class="kpi">' +
        '<p class="kpi-label">' +
        lake.label +
        "</p>" +
        '<p class="kpi-value">' +
        (level == null ? "—" : level.toFixed(2)) +
        '<span style="font-size:13px;font-weight:600;color:#5a7a86;"> m</span></p>' +
        '<div><span class="ticker ' +
        cls +
        '">' +
        mark +
        "</span></div>" +
        '<p class="kpi-sub">' +
        (vs == null ? "" : (vs >= 0 ? "+" : "") + vs.toFixed(0) + " cm vs LWD") +
        "<br>vs 1918–present mean " +
        (level == null || lake.mean == null ? "" : ((level - lake.mean) * 100 >= 0 ? "+" : "") + ((level - lake.mean) * 100).toFixed(0) + " cm") +
        "</p></div>";
    });
    kpis.innerHTML = html;
  }

  function setButtons() {
    var label = playing ? "Pause" : progress >= 1 ? "Replay 30s" : progress > 0 ? "Resume" : "Play 30s";
    toggleBtn.textContent = label;
    if (playFab) {
      playFab.textContent = progress >= 1 ? "Replay century" : "Play 30 seconds";
      playFab.style.display = playing ? "none" : "block";
    }
  }

  function frame(now) {
    if (playing) {
      progress = Math.min(1, (now - origin) / DURATION);
      if (progress >= 1) {
        progress = 1;
        playing = false;
      }
    }
    scrub.value = String(Math.round(progress * 1000));
    var t = monthT(progress);
    var ym = yearMonth(t);
    if (readout) readout.textContent = ym.year + " " + MONTHS[ym.month];
    drawProfile(progress);
    drawChart(progress);
    renderKpis(progress);
    setButtons();
    if (playing) raf = requestAnimationFrame(frame);
  }

  function play() {
    if (progress >= 1) progress = 0;
    playing = true;
    origin = performance.now() - progress * DURATION;
    setButtons();
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
  }

  function pause() {
    playing = false;
    cancelAnimationFrame(raf);
    setButtons();
    frame(performance.now());
  }

  toggleBtn.addEventListener("click", function () {
    if (playing) pause();
    else play();
  });
  if (playFab) playFab.addEventListener("click", play);
  scrub.addEventListener("input", function () {
    playing = false;
    progress = Number(scrub.value) / 1000;
    cancelAnimationFrame(raf);
    frame(performance.now());
  });
  window.addEventListener("resize", function () {
    frame(performance.now());
  });
  if (bg && !bg.complete) {
    bg.addEventListener("load", function () {
      frame(performance.now());
    });
  }

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  frame(performance.now());
  if (!reduced && "IntersectionObserver" in window) {
    var started = false;
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting && !started) {
            started = true;
            play();
          }
        });
      },
      { threshold: 0.45 }
    );
    io.observe(stage);
  }
})();
