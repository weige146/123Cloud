import { ref, computed, onMounted, onBeforeUnmount } from "vue";

const windowWidth = ref(typeof window !== "undefined" ? window.innerWidth : 1440);
const windowHeight = ref(typeof window !== "undefined" ? window.innerHeight : 900);
let subscribers = 0;

function onResize() {
  windowWidth.value = window.innerWidth;
  windowHeight.value = window.innerHeight;
}

function subscribe() {
  subscribers += 1;
  if (subscribers === 1) {
    onResize();
    window.addEventListener("resize", onResize, { passive: true });
  }
}

function unsubscribe() {
  subscribers = Math.max(0, subscribers - 1);
  if (subscribers === 0) {
    window.removeEventListener("resize", onResize);
  }
}

export function useResponsive() {
  onMounted(subscribe);
  onBeforeUnmount(unsubscribe);

  const isMobile = computed(() => windowWidth.value < 768);
  const isTablet = computed(() => windowWidth.value >= 768 && windowWidth.value < 1100);
  const isDesktop = computed(() => windowWidth.value >= 1100);
  const isCompact = computed(() => windowWidth.value < 375);

  const drawerPermanent = computed(() => windowWidth.value >= 768);
  const drawerRail = computed(() => windowWidth.value >= 768 && windowWidth.value < 1100);
  const drawerTemporary = computed(() => windowWidth.value < 768);

  return {
    windowWidth,
    windowHeight,
    isMobile,
    isTablet,
    isDesktop,
    isCompact,
    drawerPermanent,
    drawerRail,
    drawerTemporary,
  };
}
