import { api } from "./common.js";

const home = (user) => (user.role === "student" ? "/chat" : "/admin");

// Already logged in? Skip the form.
fetch("/api/auth/me", { credentials: "same-origin" })
  .then((r) => (r.ok ? r.json() : null))
  .then((u) => { if (u) location.href = home(u); });

document.getElementById("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("error");
  const btn = e.target.querySelector("button");
  err.textContent = "";
  btn.disabled = true;
  try {
    const user = await api("/api/auth/login", {
      method: "POST",
      body: { email: document.getElementById("email").value, password: document.getElementById("password").value },
    });
    location.href = home(user);
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    btn.disabled = false;
  }
});
