let websocket = null;
let actionContext = null;
let actionUuid = null;
let currentDelay = 10;

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
  actionContext = parsedActionInfo.context;
  actionUuid = parsedActionInfo.action;
  websocket = new WebSocket(`ws://127.0.0.1:${port}`);

  websocket.onopen = () => {
    websocket.send(JSON.stringify({ event: registerEvent, uuid }));
    websocket.send(JSON.stringify({ event: "getSettings", action: actionUuid, context: actionContext }));
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
    const buffer = await readFileAsArrayBuffer(file);
    const b64 = arrayBufferToBase64(buffer);
    sendToPlugin({
      op: "storeB64",
      fileName: file.name,
      byteLength: file.size,
      b64,
      delayMs: currentDelay
    });
    applyState({
      op: "stored",
      ok: true,
      fileName: file.name,
      byteLength: file.size,
      b64Length: b64.length,
      delayMs: currentDelay
    });
  } catch (error) {
    storedState.textContent = `変換失敗: ${error?.message || error}`;
    console.error(error);
  } finally {
    setBusy(false);
  }
}

function readFileAsArrayBuffer(file) {
  if (typeof file.arrayBuffer === "function") {
    return file.arrayBuffer();
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("FileReader failed"));
    reader.readAsArrayBuffer(file);
  });
}

function sendToPlugin(payload) {
  if (!websocket || websocket.readyState !== WebSocket.OPEN || !actionContext || !actionUuid) {
    return;
  }
  websocket.send(JSON.stringify({
    event: "sendToPlugin",
    action: actionUuid,
    context: actionContext,
    payload
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
    return 10;
  }
  return Math.min(30, Math.max(10, Math.round(value)));
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode.apply(null, chunk);
  }
  return btoa(binary);
}

renderDelay();
