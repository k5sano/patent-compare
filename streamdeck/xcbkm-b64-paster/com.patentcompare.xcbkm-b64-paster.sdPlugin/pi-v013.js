let websocket = null;
let propertyInspectorUuid = null;
let actionContext = null;
let actionUuid = null;
let currentDelay = 5;

const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const pickButton = document.getElementById("pickButton");
const delayInput = document.getElementById("delayInput");
const delayValue = document.getElementById("delayValue");
const storedState = document.getElementById("storedState");
const fileName = document.getElementById("fileName");
const b64Length = document.getElementById("b64Length");

window.connectElgatoStreamDeckSocket = (port, uuid, registerEvent, info, actionInfo) => {
  const parsedActionInfo = JSON.parse(actionInfo);
  propertyInspectorUuid = uuid;
  actionContext = parsedActionInfo.context;
  actionUuid = parsedActionInfo.action;
  websocket = new WebSocket(`ws://127.0.0.1:${port}`);

  websocket.onopen = () => {
    websocket.send(JSON.stringify({ event: registerEvent, uuid }));
    websocket.send(JSON.stringify({ event: "getSettings", action: actionUuid, context: propertyInspectorUuid }));
    sendToPlugin({ op: "requestState" });
  };

  websocket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.event === "didReceiveSettings") {
      applyState({ op: "state", ...(message.payload?.settings || {}) });
    } else if (message.event === "sendToPropertyInspector") {
      applyState(message.payload || {});
    }
  };
};

pickButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files && fileInput.files.length > 0) {
    handleFile(fileInput.files[0]);
  }
});

dropZone.addEventListener("dragover", event => {
  event.preventDefault();
  dropZone.classList.add("is-over");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("is-over");
});

dropZone.addEventListener("drop", event => {
  event.preventDefault();
  dropZone.classList.remove("is-over");
  const file = event.dataTransfer?.files?.[0];
  if (file) {
    handleFile(file);
  }
});

delayInput.addEventListener("input", () => {
  currentDelay = clampDelay(Number(delayInput.value));
  renderDelay();
  sendToPlugin({ op: "setDelay", delayMs: currentDelay });
});

async function handleFile(file) {
  setBusy(true);
  try {
    const b64 = await readFileAsBase64(file);
    storedState.textContent = "転送中";
    const settings = {
      b64,
      fileName: file.name,
      byteLength: file.size,
      delayMs: currentDelay
    };
    setSettings(settings);
    sendToPlugin({
      op: "storeB64",
      fileName: file.name,
      byteLength: file.size,
      b64,
      delayMs: currentDelay
    });
  } catch (error) {
    storedState.textContent = `変換失敗: ${error?.message || error}`;
    console.error(error);
  } finally {
    setBusy(false);
  }
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      if (comma < 0) {
        reject(new Error("DataURL conversion did not return Base64 payload"));
        return;
      }
      resolve(result.slice(comma + 1));
    };
    reader.onerror = () => reject(reader.error || new Error("FileReader failed"));
    reader.readAsDataURL(file);
  });
}

function sendToPlugin(payload) {
  if (!websocket || websocket.readyState !== WebSocket.OPEN || !propertyInspectorUuid || !actionUuid) {
    storedState.textContent = "本体未接続";
    return;
  }
  websocket.send(JSON.stringify({
    event: "sendToPlugin",
    action: actionUuid,
    context: propertyInspectorUuid,
    payload
  }));
}

function setSettings(settings) {
  if (!websocket || websocket.readyState !== WebSocket.OPEN || !propertyInspectorUuid || !actionUuid) {
    storedState.textContent = "設定保存未接続";
    return;
  }
  websocket.send(JSON.stringify({
    event: "setSettings",
    action: actionUuid,
    context: propertyInspectorUuid,
    payload: settings
  }));
}

function applyState(payload) {
  if (typeof payload.b64 !== "undefined" && typeof payload.b64Length === "undefined") {
    payload.b64Length = String(payload.b64 || "").length;
    payload.ok = payload.b64Length > 0;
  }
  if (typeof payload.delayMs !== "undefined") {
    currentDelay = clampDelay(Number(payload.delayMs));
    delayInput.value = String(currentDelay);
    renderDelay();
  }
  if (typeof payload.fileName !== "undefined") {
    fileName.textContent = payload.fileName || "-";
  }
  if (typeof payload.b64Length !== "undefined") {
    b64Length.textContent = `${Number(payload.b64Length || 0).toLocaleString()} chars`;
  }
  if (typeof payload.ok !== "undefined") {
    storedState.textContent = payload.ok ? "保持済み" : "未設定";
  } else if (payload.op === "typing") {
    storedState.textContent = "ペースト中";
  } else if (payload.op === "typed") {
    storedState.textContent = "保持済み";
  }
}

function renderDelay() {
  delayValue.textContent = `${currentDelay} ms`;
}

function setBusy(isBusy) {
  dropZone.classList.toggle("is-busy", isBusy);
  pickButton.disabled = isBusy;
}

function clampDelay(value) {
  if (!Number.isFinite(value)) {
    return 5;
  }
  return Math.min(10, Math.max(1, Math.round(value)));
}

renderDelay();
