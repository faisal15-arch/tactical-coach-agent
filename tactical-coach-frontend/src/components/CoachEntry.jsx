import { useState } from "react";
import { loginStudent, registerStudent } from "../api/client";
import "./CoachEntry.css";

export default function CoachEntry({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [fullName, setFullName] = useState("");
  const [studentId, setStudentId] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");

  const changeMode = (nextMode) => {
    setMode(nextMode);
    setError("");
    setStatus("idle");
  };

  const submit = async (event) => {
    event.preventDefault();
    if (mode === "register" && password !== confirmation) {
      setError("Passwords do not match");
      return;
    }

    setStatus("loading");
    setError("");
    try {
      const data = mode === "register"
        ? await registerStudent(fullName.trim(), studentId.trim(), password)
        : await loginStudent(studentId.trim(), password);
      onAuthenticated(data.student);
    } catch (err) {
      setError(err.message);
      setStatus("idle");
    }
  };

  const isRegistering = mode === "register";

  return (
    <div className="entry">
      <form className="entry__panel" onSubmit={submit}>
        <p className="entry__eyebrow">Tactical Coach</p>
        <h2 className="entry__title">
          {isRegistering ? "Create student account" : "Student sign in"}
        </h2>

        <p className="entry__lede">
          {isRegistering
            ? "Create your account, then enter the live tactical console."
            : "Sign in to access live match intelligence and the tactical console."}
        </p>

        <div className="entry__switch" aria-label="Authentication mode">
          <button
            className={!isRegistering ? "is-active" : ""}
            type="button"
            onClick={() => changeMode("login")}
          >
            Sign in
          </button>
          <button
            className={isRegistering ? "is-active" : ""}
            type="button"
            onClick={() => changeMode("register")}
          >
            Create account
          </button>
        </div>

        {isRegistering && (
          <>
            <label className="entry__label" htmlFor="student-name">
              Full name
            </label>
            <input
              id="student-name"
              className="entry__input"
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              placeholder="Enter your full name"
              autoComplete="name"
              autoFocus
              required
            />
          </>
        )}

        <label className="entry__label" htmlFor="student-id">
          Student ID
        </label>
        <input
          id="student-id"
          className="entry__input"
          value={studentId}
          onChange={(event) => setStudentId(event.target.value)}
          placeholder="For example, FAISAL123"
          autoComplete="username"
          autoFocus={!isRegistering}
          required
        />

        <label className="entry__label" htmlFor="student-password">
          Password
        </label>
        <input
          id="student-password"
          className="entry__input"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Enter your password"
          autoComplete={isRegistering ? "new-password" : "current-password"}
          minLength={isRegistering ? 10 : undefined}
          required
        />

        {isRegistering && (
          <>
            <p className="entry__hint">Use at least 10 characters.</p>
            <label className="entry__label" htmlFor="student-confirmation">
              Confirm password
            </label>
            <input
              id="student-confirmation"
              className="entry__input"
              type="password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              placeholder="Enter the password again"
              autoComplete="new-password"
              minLength={10}
              required
            />
          </>
        )}

        {error && (
          <p className="entry__error" role="alert">
            {error}
          </p>
        )}

        <button
          className="entry__button"
          type="submit"
          disabled={status === "loading"}
        >
          {status === "loading"
            ? (isRegistering ? "Creating account…" : "Signing in…")
            : (isRegistering ? "Create account" : "Sign in")}
        </button>

        <p className="entry__note">
          Your password is stored as a secure hash, not as plain text.
        </p>
      </form>
    </div>
  );
}
