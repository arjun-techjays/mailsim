import { render, screen } from "@testing-library/react";
import { ApiError } from "../api/client";
import { ErrorDisplay } from "./ErrorDisplay";

describe("ErrorDisplay", () => {
  it("shows code, status, message, field errors, details and request id", () => {
    const err = new ApiError({
      kind: "http",
      status: 400,
      code: "INVALID_REQUEST",
      message: "request validation failed",
      details: {
        errors: [
          { loc: ["body", "message", "from"], msg: "bad email" },
          { loc: "weird", msg: 3 },
          { loc: ["body"] },
          null,
        ],
        hint: "x",
      },
      requestId: "req-42",
    });
    render(<ErrorDisplay error={err} title="Injection rejected" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "Injection rejected: INVALID_REQUEST (HTTP 400)",
    );
    expect(alert).toHaveTextContent("request validation failed");
    const fields = screen.getByRole("list", { name: "Field errors" });
    expect(fields).toHaveTextContent("message.from: bad email");
    expect(fields).toHaveTextContent("(request): 3");
    expect(fields).toHaveTextContent("(request):");
    expect(screen.getByText("Details")).toBeInTheDocument();
    expect(screen.getByTestId("request-id")).toHaveTextContent("req-42");
  });

  it("adds role hints for permission errors and omits missing request ids", () => {
    render(
      <ErrorDisplay
        error={
          new ApiError({
            kind: "http",
            status: 403,
            code: "PERMISSION_DENIED",
            message: "no",
          })
        }
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "PERMISSION_DENIED (HTTP 403)",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("role is not allowed");
    expect(screen.queryByTestId("request-id")).toBeNull();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("explains upstream (LLM provider) outages as not caused by the request", () => {
    const err = new ApiError({
      kind: "http",
      status: 503,
      code: "UPSTREAM_UNAVAILABLE",
      message: "anthropic provider failed (overloaded)",
      details: { retryable: true },
      requestId: "r1",
    });
    render(<ErrorDisplay error={err} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "UPSTREAM_UNAVAILABLE (HTTP 503)",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "not caused by your request",
    );
  });

  it("labels timeouts and network errors", () => {
    const { rerender } = render(
      <ErrorDisplay
        error={
          new ApiError({ kind: "timeout", code: "TIMEOUT", message: "slow" })
        }
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Request timed out");
    rerender(
      <ErrorDisplay
        error={
          new ApiError({
            kind: "network",
            code: "NETWORK_ERROR",
            message: "down",
          })
        }
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Network error");
  });

  it("lets the caller replace or drop the generic hint", () => {
    const timeout = new ApiError({ kind: "timeout", code: "TIMEOUT", message: "slow" });
    const { rerender } = render(<ErrorDisplay error={timeout} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Try again.");
    rerender(<ErrorDisplay error={timeout} hint={null} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Try again");
    rerender(<ErrorDisplay error={timeout} hint="Use the button below." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Use the button below.");
  });

  it("renders unknown errors", () => {
    const { rerender } = render(<ErrorDisplay error={new Error("boom")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong");
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    rerender(<ErrorDisplay error="plain" title="T" />);
    expect(screen.getByRole("alert")).toHaveTextContent("T");
    expect(screen.getByRole("alert")).toHaveTextContent("plain");
  });
});
