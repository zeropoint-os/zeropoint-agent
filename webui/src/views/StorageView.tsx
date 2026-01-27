import React from 'react';
import DisksPane from './DisksPane';
import './Views.css';

export default function StorageView() {
  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Storage</h1>
      </div>

      <DisksPane />
    </div>
  );
}
