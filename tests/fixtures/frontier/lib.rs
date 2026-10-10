pub fn unsupported() -> u32 {
    unsafe { core::arch::asm!("nop"); }
    1
}

pub fn caller() -> std::time::Instant {
    let _value = unsupported();
    std::time::Instant::now()
}
