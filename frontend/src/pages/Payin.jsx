import React from 'react';
import PayinWorkspace from '../components/payin/PayinWorkspace';

export default function PayinPage() {
  return (
    <div style={{ padding: '20px', minHeight: '100vh' }}>
      <PayinWorkspace showHeading={true} mode="viewer" />
    </div>
  );
}
