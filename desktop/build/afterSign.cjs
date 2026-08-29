/* electron-builder afterSign hook: ad-hoc sign the macOS app.

   Without any signature a quarantined (browser-downloaded) app is judged
   "damaged" by Gatekeeper and cannot even be opened via right-click.  An
   ad-hoc signature turns that into the recoverable "unidentified
   developer" path instead. */

const { execSync } = require("child_process");
const path = require("path");

exports.default = async function afterSign(context) {
  if (context.electronPlatformName !== "darwin") return;
  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);
  console.log(`Ad-hoc signing ${appPath}`);
  execSync(`codesign --force --deep --sign - "${appPath}"`, { stdio: "inherit" });
  execSync(`codesign --verify --deep --strict "${appPath}"`, { stdio: "inherit" });
  console.log("Ad-hoc signing verified");
};
