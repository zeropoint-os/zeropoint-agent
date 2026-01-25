import React from 'react';
import { Link } from 'react-router-dom';
import './Navigation.css';

interface NavigationProps {
  isOpen: boolean;
  currentView: string;
  onViewChange: (view: any) => void;
  bootComplete?: boolean;
}

interface NavItem {
  id: string;
  label: string;
  path: string;
  icon: React.ReactNode;
}

const allNavItems: NavItem[] = [
  {
    id: 'boot',
    label: 'Boot',
    path: '/boot',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M6 9l6-7 6 7"></path>
        <path d="M6 15h12"></path>
        <rect x="2" y="15" width="20" height="7" rx="1"></rect>
      </svg>
    ),
  },
  {
    id: 'bundles',
    label: 'Bundles',
    path: '/bundles',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M6.2 2h11.6c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H6.2c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2z"></path>
        <path d="M12 10v6M9 13h6"></path>
      </svg>
    ),
  },
  {
    id: 'modules',
    label: 'Modules',
    path: '/modules',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="3" width="7" height="7"></rect>
        <rect x="14" y="3" width="7" height="7"></rect>
        <rect x="14" y="14" width="7" height="7"></rect>
        <rect x="3" y="14" width="7" height="7"></rect>
      </svg>
    ),
  },
  {
    id: 'links',
    label: 'Links',
    path: '/links',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
      </svg>
    ),
  },
  {
    id: 'exposures',
    label: 'Exposures',
    path: '/exposures',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
        <circle cx="12" cy="12" r="3"></circle>
      </svg>
    ),
  },
  {
    id: 'jobs',
    label: 'Jobs',
    path: '/jobs',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect>
        <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path>
      </svg>
    ),
  },
  {
    id: 'storage',
    label: 'Storage',
    path: '/storage',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="4" width="18" height="6" rx="2"></rect>
        <rect x="3" y="14" width="18" height="6" rx="2"></rect>
      </svg>
    ),
  },
];

const getNavItems = (bootComplete: boolean): NavItem[] => {
  if (bootComplete) {
    // Boot complete: move boot to bottom
    return allNavItems.slice(1).concat([allNavItems[0]]);
  }
  // Boot not complete: show boot first
  return allNavItems;
};

export default function Navigation({ isOpen, currentView, onViewChange, bootComplete = false }: NavigationProps) {
  const navItems = getNavItems(bootComplete);
  return (
    <>
      {/* Overlay for mobile */}
      {isOpen && (
        <div className="nav-overlay" onClick={() => onViewChange(currentView)} />
      )}
      {/* Navigation */}
      <nav className={`navigation ${isOpen ? 'open' : ''}`}>
        <ul className="nav-list">
          {navItems.map((item) => {
            const isDisabled = !bootComplete && item.id !== 'boot';
            return (
              <li key={item.id}>
                <Link
                  to={item.path}
                  className={`nav-item ${currentView === item.id ? 'active' : ''} ${isDisabled ? 'disabled' : ''}`}
                  title={isDisabled ? 'Boot must complete first' : item.label}
                  onClick={(e) => {
                    if (isDisabled) {
                      e.preventDefault();
                      return;
                    }
                    onViewChange(item.id);
                  }}
                >
                  <span className="nav-icon">{item.icon}</span>
                  <span className="nav-label">{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
