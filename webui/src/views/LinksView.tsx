import React, { useState, useEffect } from 'react';
import './Views.css';

interface Link {
  id?: string;
  source?: string;
  target?: string;
  tags?: string[];
  [key: string]: any;
}

export default function LinksView() {
  const [links, setLinks] = useState<Link[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchLinks();
  }, []);

  const fetchLinks = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/links');
      if (!response.ok) {
        throw new Error(`Failed to fetch links: ${response.statusText}`);
      }
      const data = await response.json();
      const linkList = Array.isArray(data) ? data : (data.links || data.data || []);
      setLinks(linkList);
    } catch (err) {
      console.error('Error loading links:', err);
      setLinks([]);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateLink = () => {
    // TODO: Show create link modal
    console.log('Create link');
  };

  const handleDeleteLink = (linkId: string) => {
    // TODO: Show delete confirmation
    console.log('Delete link:', linkId);
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Links</h1>
        <button className="button button-primary" onClick={handleCreateLink}>
          <span>+</span> Create Link
        </button>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading links...</p>
        </div>
      ) : links.length === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
          </svg>
          <h2>No links created</h2>
          <p>Create links between modules to establish connections.</p>
          <button className="button button-primary" onClick={handleCreateLink}>
            Create Link
          </button>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Target</th>
                <th>Tags</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {links.map((link, idx) => {
                const linkId = link.id || `link-${idx}`;
                return (
                  <tr key={linkId}>
                    <td>{link.source || 'N/A'}</td>
                    <td>{link.target || 'N/A'}</td>
                    <td>
                      {link.tags?.map((tag) => (
                        <span key={tag} className="tag">
                          {tag}
                        </span>
                      ))}
                    </td>
                    <td>
                      <button
                        className="button button-small button-danger"
                        onClick={() => handleDeleteLink(linkId)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
