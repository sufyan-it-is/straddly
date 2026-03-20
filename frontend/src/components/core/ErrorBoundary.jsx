import React from 'react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display:        'flex',
          flexDirection:  'column',
          alignItems:     'center',
          justifyContent: 'center',
          height:         '100vh',
          background:     '#0d1117',
          color:          '#e6edf3',
          gap:            '16px',
        }}>
          <h2 style={{ color: '#f85149', fontSize: '1.4rem' }}>Something went wrong</h2>
          <p style={{ color: '#7d8590', maxWidth: '480px', textAlign: 'center' }}>
            {this.state.error?.message || 'An unexpected error occurred.'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              padding:      '8px 20px',
              background:   '#388bfd',
              color:        '#fff',
              border:       'none',
              borderRadius: '6px',
              cursor:       'pointer',
            }}
          >
            Try Again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
