pub trait IO {
    fn value(&self) -> u32;
}

pub struct Store;

impl IO for Store {
    fn value(&self) -> u32 {
        1
    }
}

fn space(io: &mut dyn IO) -> u32 {
    io.value()
}

pub fn call(store: &mut Store) -> u32 {
    space(store)
}
