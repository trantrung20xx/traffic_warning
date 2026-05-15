import React from "react";

export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // Giữ log đầy đủ để truy vết lỗi runtime thay vì che lỗi trắng màn hình.
    console.error("Unhandled React runtime error", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="app-crash-boundary">
          <div className="message-bar warning">
            <strong>Giao diện gặp lỗi runtime.</strong>
            <div>{this.state.error?.message || "Lỗi không xác định."}</div>
            <button className="button secondary compact-button" type="button" onClick={() => window.location.reload()}>
              Tải lại giao diện
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
