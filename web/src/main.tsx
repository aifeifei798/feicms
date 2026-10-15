import ReactDOM from "react-dom";
import App from "./App";
import Site from "./site/Site";
import "./styles.css";

// `/` and `/video/{id}` are the public cinema-style front site; everything under
// `/admin` is the DaisyUI management panel.
const path = window.location.pathname;
const isAdmin = path === "/admin" || path.startsWith("/admin/");

if (!isAdmin) document.documentElement.setAttribute("data-theme", "dark");

ReactDOM.render(isAdmin ? <App /> : <Site />, document.getElementById("root"));
