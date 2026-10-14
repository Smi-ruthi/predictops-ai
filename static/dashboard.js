
let latencyChart;

function formatUptime(seconds) {
  const s = Number(seconds || 0);
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const secs = s % 60;

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function setBar(id, value) {
  const el = document.getElementById(id);
  if (el) {
    el.style.width = `${Math.max(
      0,
      Math.min(100, Number(value) || 0)
    )}%`;
  }
}

function initChart() {
  const canvas = document.getElementById("latencyChart");

  if (!canvas || !window.Chart) return;

  latencyChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label: "Blog latency (ms)",
        data: [],
        borderColor: "#6b5ce7",
        backgroundColor: "rgba(107,92,231,.09)",
        fill: true,
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 3,
        pointHoverRadius: 5
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: {
        intersect: false,
        mode: "index"
      },
      plugins: {
        legend: { display: false }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            color: "#9aa3b1",
            maxTicksLimit: 6,
            font: { size: 10 }
          }
        },
        y: {
          beginAtZero: true,
          grid: { color: "#eef1f6" },
          ticks: {
            color: "#9aa3b1",
            font: { size: 10 }
          }
        }
      }
    }
  });
}

async function refreshMetrics() {
  try {
    // Read metrics from the separate personal blog.
    const response = await fetch("/api/blog-metrics", {
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error("Blog metrics endpoint returned an error");
    }

    const data = await response.json();

    setText("connectionStatus", "Blog monitoring active");
    setText("latency", Number(data.avg_latency_ms || 0).toFixed(1));
    setText("requestRate", data.request_rate_per_min ?? 0);
    setText("errorRate", Number(data.error_rate_pct || 0).toFixed(1));
    setText("cpu", Number(data.cpu_percent || 0).toFixed(1));
    setText("memory", Number(data.memory_percent || 0).toFixed(1));

    // The blog metrics endpoint does not currently provide blog uptime.
    // Do not display the dashboard server's uptime as the blog's uptime.
    setText("uptime", "N/A");

    setBar("cpuBar", data.cpu_percent);
    setBar("memoryBar", data.memory_percent);

    setText(
      "lastUpdated",
      `Updated ${new Date().toLocaleTimeString()}`
    );

    const points = data.recent || [];
    const empty = document.getElementById("chartEmpty");

    if (empty) {
      empty.style.display = points.length ? "none" : "block";
    }

    if (latencyChart) {
      latencyChart.data.labels = points.map((point, index) => {
        const timestamp = point.timestamp
          ? new Date(point.timestamp)
          : null;

        return timestamp && !Number.isNaN(timestamp.getTime())
          ? timestamp.toLocaleTimeString()
          : `#${index + 1}`;
      });

      latencyChart.data.datasets[0].data = points.map(
        point => Number(point.latency_ms || 0)
      );

      latencyChart.update("none");
    }

    await refreshAlerts();

  } catch (error) {
    setText("connectionStatus", "Waiting for blog metrics");
    console.error("Could not refresh blog metrics:", error);
  }
}

async function refreshAlerts() {
  try {
    const response = await fetch("/api/alerts", {
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error("Alerts endpoint returned an error");
    }

    const data = await response.json();
    const list = document.getElementById("alertsList");

    if (!list) return;

    list.replaceChildren();

    (data.alerts || []).forEach(alert => {
      const item = document.createElement("div");
      item.className = `alert-row ${alert.level || ""}`;
      item.textContent = alert.message;
      list.appendChild(item);
    });

  } catch (error) {
    console.error("Could not refresh alerts:", error);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initChart();
  refreshMetrics();

  // Refresh the dashboard every five seconds.
  window.setInterval(refreshMetrics, 5000);
});

async function loadBlogVisitors() {
  const tableBody = document.getElementById("visitorsTableBody");
  const visitorCount = document.getElementById("visitorCount");

  if (!tableBody || !visitorCount) return;

  try {
    const response = await fetch("/api/blog-visitors");

    if (!response.ok) {
      throw new Error("Unable to fetch visitor data");
    }

    const data = await response.json();

    const visitors = Array.isArray(data)
      ? data
      : (
          data.visitors ||
          data.recent_visitors ||
          data.requests ||
          data.data ||
          []
        );

    visitorCount.textContent = `${visitors.length} recent requests`;

    if (visitors.length === 0) {
      tableBody.replaceChildren();

      const row = tableBody.insertRow();
      const cell = row.insertCell();
      cell.colSpan = 5;
      cell.textContent =
        "No recent visitors. Open your blog to generate traffic.";
      return;
    }

    tableBody.replaceChildren();

    visitors.forEach((visitor) => {
      const row = document.createElement("tr");

      const values = [
        visitor.client_ip || "Unknown",
        visitor.route || "/",
        visitor.method || "GET",
        visitor.status_code ?? "N/A",
        visitor.latency_ms != null
          ? `${Number(visitor.latency_ms).toFixed(1)} ms`
          : "N/A"
      ];

      values.forEach((value, index) => {
        const cell = row.insertCell();
        cell.textContent = value;

        if (index === 0) {
          cell.className = "visitor-ip";
        }

        if (index === 3) {
          const status = Number(value);

          if (status >= 500) {
            cell.className = "status-error";
          } else if (status >= 400) {
            cell.className = "status-warning";
          } else {
            cell.className = "status-success";
          }
        }
      });

      tableBody.appendChild(row);
    });
  } catch (error) {
    console.error("Blog visitor loading error:", error);

    visitorCount.textContent = "Connection error";
    tableBody.replaceChildren();

    const row = tableBody.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 5;
    cell.textContent =
      "Could not load visitors. Check the PredictOps backend.";
  }
}

loadBlogVisitors();
setInterval(loadBlogVisitors, 5000);
