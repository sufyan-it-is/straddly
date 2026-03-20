declare module 'vanilla-tilt' {
    export interface TiltOptions {
        max?: number;
        perspective?: number;
        scale?: number;
        speed?: number;
        transition?: boolean;
        axis?: string | null;
        reset?: boolean;
        easing?: string;
        glare?: boolean;
        'max-glare'?: number;
        'glare-prerender'?: boolean;
        'mouse-event-element'?: HTMLElement | string | null;
        'full-page-listening'?: boolean;
        gyroscope?: boolean;
        gyroscopeMinAngleX?: number;
        gyroscopeMaxAngleX?: number;
        gyroscopeMinAngleY?: number;
        gyroscopeMaxAngleY?: number;
    }

    export interface HTMLVanillaTiltElement extends HTMLElement {
        vanillaTilt: {
            destroy: () => void;
            getValues: () => any;
            reset: () => void;
        };
    }

    export default class VanillaTilt {
        static init(element: HTMLElement | HTMLElement[] | NodeListOf<HTMLElement>, options?: TiltOptions): void;
    }
}
