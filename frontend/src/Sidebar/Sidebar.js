import React, { useState } from "react";
import "./Sidebar.css";

import {
  LuLayoutDashboard,
  LuUsers,
  LuFolderTree,
  LuMonitor,
  LuBell,
  LuShieldCheck,
  LuChartNoAxesColumn,
  LuClipboardList,
  LuPlug,
  LuSettings,
  LuRocket,
  LuUserRound,
  LuChevronDown,
  LuChevronsLeft,
} from "react-icons/lu";

const Sidebar = ({ isOpen, onClose }) => {
  const [activeMenu, setActiveMenu] = useState("dashboard");

  const menuItems = [
    {
      id: "dashboard",
      label: "Dashboard",
      icon: LuLayoutDashboard,
      href: "/dashboard",
    },
    {
      id: "agents",
      label: "Agents",
      icon: LuUsers,
      href: "/agents",
    },
    {
      id: "groups",
      label: "Groups",
      icon: LuFolderTree,
      href: "/groups",
    },
    {
      id: "endpoints",
      label: "Endpoints",
      icon: LuMonitor,
      href: "/endpoints",
    },
    {
      id: "alerts",
      label: "Alerts",
      icon: LuBell,
      href: "/alerts",
      badge: 3,
    },
    {
      id: "policies",
      label: "Policies",
      icon: LuShieldCheck,
      href: "/policies",
    },
    {
      id: "reports",
      label: "SOC2Reports",
      icon: LuChartNoAxesColumn,
      href: "/reports/soc2",
    },
    {
      id: "activity",
      label: "Activity Logs",
      icon: LuClipboardList,
      href: "/activity-logs",
    },
    {
      id: "integrations",
      label: "Integrations",
      icon: LuPlug,
      href: "/integrations",
    },
    {
      id: "users",
      label: "Users",
      icon: LuUsers,
      href: "/access",
    },
    {
      id: "settings",
      label: "Settings",
      icon: LuSettings,
      href: "/settings",
    },
  ];

  const handleMenuClick = (id) => {
    setActiveMenu(id);

    // Mobile par menu click ke baad sidebar close hoga
    if (window.innerWidth <= 768 && onClose) {
      onClose();
    }
  };

  return (
    <>
      {/* Mobile overlay */}
      <div
        className={`sidebar-overlay ${isOpen ? "visible" : ""}`}
        onClick={onClose}
      ></div>

      <aside className={`sidebar ${isOpen ? "open" : ""}`}>

        {/* =================================
            SIDEBAR HEADER
            ================================= */}
        <div className="sidebar-header">

  <div className="sidebar-brand">

    <div className="sidebar-brand-icon">
      G
    </div>

    <span className="sidebar-brand-name">
      GuardLynx
    </span>

  </div>

  <button
    className="sidebar-menu-toggle"
    onClick={onClose}
    aria-label="Close sidebar"
  >
    ☰
  </button>

</div>


        {/* =================================
            NAVIGATION
            ================================= */}
        <nav className="sidebar-nav">

          <ul className="nav-menu">

            {menuItems.map((item) => {
              const Icon = item.icon;

              return (
                <li key={item.id}>

                  <a
                    href={`/app${item.href}`}
                    className={`nav-item ${
                      activeMenu === item.id ? "active" : ""
                    }`}
                    onClick={() => handleMenuClick(item.id)}
                  >

                    <span className="nav-icon">
                      <Icon />
                    </span>

                    <span className="nav-label">
                      {item.label}
                    </span>

                    {item.badge && (
                      <span className="nav-badge">
                        {item.badge}
                      </span>
                    )}

                  </a>

                </li>
              );
            })}

          </ul>

        </nav>


        {/* =================================
            BOTTOM AREA
            ================================= */}
        <div className="sidebar-bottom">

          {/* =================================
              UPGRADE CARD
              ================================= */}
          <div className="sidebar-upgrade-card">

            <div className="upgrade-icon">
              <LuRocket />
            </div>

            <h4>
              Upgrade to Pro
            </h4>

            <p>
              Unlock advanced features,
              custom alerts, insights and more.
            </p>

            <button className="upgrade-button">
              Upgrade Now →
            </button>

          </div>


          {/* =================================
              USER CARD
              ================================= */}
          <div className="sidebar-user-card">

            <div className="sidebar-user-avatar">
              <LuUserRound />
            </div>

            <div className="sidebar-user-info">

              <span className="sidebar-user-name">
                Admin
              </span>

              <span className="sidebar-user-role">
                Super Administrator
              </span>

            </div>

            <span className="sidebar-user-arrow">
              <LuChevronDown />
            </span>

          </div>


          {/* =================================
              COLLAPSE
              ================================= */}
          <button
            className="sidebar-collapse"
            onClick={onClose}
          >

            <span className="collapse-icon">
              <LuChevronsLeft />
            </span>

            <span>
              Collapse
            </span>

          </button>

        </div>

      </aside>
    </>
  );
};

export default Sidebar;