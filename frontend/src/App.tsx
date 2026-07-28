import {
  AssessmentOutlined,
  AutoGraphOutlined,
  DashboardOutlined,
  FileDownloadOutlined,
  MenuBookOutlined,
  PointOfSaleOutlined,
  SettingsOutlined,
} from "@mui/icons-material";
import {
  AppBar,
  Box,
  Container,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Typography,
} from "@mui/material";
import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

const drawerWidth = 248;

type NavigationItem = {
  label: string;
  path: string;
  icon: ReactNode;
};

const navigation: NavigationItem[] = [
  { label: "Dashboard", path: "/", icon: <DashboardOutlined /> },
  { label: "Earn Journal", path: "/earn-journal", icon: <MenuBookOutlined /> },
  { label: "Sales", path: "/sales", icon: <PointOfSaleOutlined /> },
  {
    label: "Recommendations",
    path: "/recommendations",
    icon: <AutoGraphOutlined />,
  },
  { label: "Audit", path: "/audit", icon: <AssessmentOutlined /> },
  { label: "Exports", path: "/exports", icon: <FileDownloadOutlined /> },
  { label: "Settings", path: "/settings", icon: <SettingsOutlined /> },
];

function PlaceholderPage({ title }: { title: string }) {
  return (
    <Box>
      <Typography component="h1" variant="h4" gutterBottom>
        {title}
      </Typography>
      <Typography color="text.secondary">
        This area is prepared for a future sprint.
      </Typography>
    </Box>
  );
}

export function App() {
  const location = useLocation();

  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <AppBar
        position="fixed"
        sx={{ zIndex: (currentTheme) => currentTheme.zIndex.drawer + 1 }}
      >
        <Toolbar>
          <Typography component="div" variant="h6">
            Kraken Tax Companion
          </Typography>
        </Toolbar>
      </AppBar>
      <Drawer
        variant="permanent"
        sx={{
          width: drawerWidth,
          flexShrink: 0,
          "& .MuiDrawer-paper": {
            boxSizing: "border-box",
            width: drawerWidth,
          },
        }}
      >
        <Toolbar />
        <List component="nav" aria-label="Main navigation">
          {navigation.map((item) => (
            <ListItemButton
              component={Link}
              key={item.path}
              selected={location.pathname === item.path}
              to={item.path}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.label} />
            </ListItemButton>
          ))}
        </List>
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, pt: 12, pb: 6 }}>
        <Container maxWidth="lg">
          <Routes>
            {navigation.map((item) => (
              <Route
                element={<PlaceholderPage title={item.label} />}
                key={item.path}
                path={item.path}
              />
            ))}
            <Route path="*" element={<Navigate replace to="/" />} />
          </Routes>
        </Container>
      </Box>
    </Box>
  );
}
